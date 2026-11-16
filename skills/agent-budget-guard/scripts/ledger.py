#!/usr/bin/env python3
"""Record what was actually spent, and summarize it.

Examples
  # After a job: record the usage numbers the API returned
  python ledger.py record --model claude-sonnet-5-5 --input-tokens 41230 --output-tokens 7902 --note "summarize docs"

  # Unit-priced tool
  python ledger.py record --unit example-video-credits --units 120 --note "trailer v2"

  # Didn't get real usage numbers? Mark the entry as an estimate
  python ledger.py record --model gpt-6.1-sol --input-tokens 20000 --output-tokens 3000 --estimated

  python ledger.py summary --period today
  python ledger.py report --period month --out spend-report.md

The ledger is a JSONL file at ~/.budget-guard/ledger.jsonl (override with BUDGET_GUARD_LEDGER).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def period_start(period: str) -> dt.date:
    t = common.today()
    return {
        "today": t,
        "week": t - dt.timedelta(days=t.weekday()),
        "month": t.replace(day=1),
        "all": dt.date.min,
    }[period]


def cmd_record(args) -> dict:
    pricing = common.load_pricing()
    if args.cost is not None:
        cost = args.cost
    elif args.unit:
        cost = common.unit_cost(pricing, args.unit, args.units)
    elif args.model:
        cost = common.token_cost(pricing, args.model, args.input_tokens, args.output_tokens,
                                 args.cached_input_tokens, args.batch)
    else:
        raise SystemExit("record needs --model, --unit or --cost")

    row = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "date": common.today().isoformat(),
        "model": args.model or args.unit or "manual",
        "input_tokens": args.input_tokens,
        "output_tokens": args.output_tokens,
        "units": args.units or None,
        "cost_usd": round(cost, 6),
        "estimated": args.estimated,
        "note": args.note,
    }
    common.append_ledger(row)
    policy = common.load_policy()
    caps = policy.get("caps", {})
    return {
        "recorded": row,
        "spent_today_usd": round(common.spent_today(), 4),
        "daily_cap_usd": caps.get("daily_usd"),
        "spent_month_usd": round(common.spent_this_month(), 4),
        "monthly_cap_usd": caps.get("monthly_usd"),
    }


def summarize(period: str) -> dict:
    start = period_start(period)
    rows = [r for r in common.read_ledger() if dt.date.fromisoformat(r["date"]) >= start]
    by_model = defaultdict(lambda: {"cost_usd": 0.0, "entries": 0, "input_tokens": 0, "output_tokens": 0})
    by_day = defaultdict(float)
    for r in rows:
        m = by_model[r["model"]]
        m["cost_usd"] += r["cost_usd"]
        m["entries"] += 1
        m["input_tokens"] += r.get("input_tokens") or 0
        m["output_tokens"] += r.get("output_tokens") or 0
        by_day[r["date"]] += r["cost_usd"]
    total = sum(r["cost_usd"] for r in rows)
    return {
        "period": period,
        "from": start.isoformat() if start != dt.date.min else None,
        "total_usd": round(total, 4),
        "entries": len(rows),
        "estimated_entries": sum(1 for r in rows if r.get("estimated")),
        "by_model": dict(sorted(((k, {**v, "cost_usd": round(v["cost_usd"], 4)}) for k, v in by_model.items()),
                                key=lambda kv: -kv[1]["cost_usd"])),
        "by_day": dict(sorted((k, round(v, 4)) for k, v in by_day.items())),
        "top_entries": sorted(rows, key=lambda r: -r["cost_usd"])[:5],
    }


def to_markdown(s: dict) -> str:
    policy = common.load_policy()
    caps = policy.get("caps", {})
    lines = [
        f"# Agent spend report — {s['period']}",
        "",
        f"_Generated {common.today().isoformat()} by agent-budget-guard_",
        "",
        f"**Total spent:** {common.money(s['total_usd'])} across {s['entries']} job(s)",
    ]
    if s["estimated_entries"]:
        n = s["estimated_entries"]
        lines.append(f"  ({n} entry is an estimate" if n == 1 else f"  ({n} entries are estimates")
        lines[-1] += ", not provider-reported usage)"
    lines += [
        "",
        "| Cap | Limit | Spent | Used |",
        "|---|---|---|---|",
    ]
    for label, key, spent in [("Today", "daily_usd", common.spent_today()),
                              ("This month", "monthly_usd", common.spent_this_month())]:
        cap = caps.get(key)
        if cap:
            lines.append(f"| {label} | {common.money(cap)} | {common.money(spent)} | {spent / cap:.0%} |")
    lines += ["", "## By model", "", "| Model | Jobs | Input tokens | Output tokens | Cost | Share |",
              "|---|---|---|---|---|---|"]
    for model, v in s["by_model"].items():
        share = v["cost_usd"] / s["total_usd"] if s["total_usd"] else 0
        lines.append(f"| {model} | {v['entries']} | {v['input_tokens']:,} | {v['output_tokens']:,} | "
                     f"{common.money(v['cost_usd'])} | {share:.0%} |")
    lines += ["", "## Most expensive jobs", "", "| Date | Model | Cost | Note |", "|---|---|---|---|"]
    for r in s["top_entries"]:
        lines.append(f"| {r['date']} | {r['model']} | {common.money(r['cost_usd'])} | {r.get('note') or ''} |")

    # Savings hint: what the top model's tokens would cost on the cheapest same-tier model
    pricing = common.load_pricing()
    hints = []
    for model, v in s["by_model"].items():
        if model not in pricing.get("models", {}) or not v["input_tokens"]:
            continue
        tier = pricing["models"][model]["tier"]
        same = [(n, common.token_cost(pricing, n, v["input_tokens"], v["output_tokens"]))
                for n, e in pricing["models"].items() if e["tier"] == tier and n != model]
        if not same:
            continue
        name, cheaper = min(same, key=lambda x: x[1])
        if cheaper < v["cost_usd"] * 0.8:
            hints.append(f"- **{model}** jobs would have cost {common.money(cheaper)} on **{name}** "
                         f"(same tier) instead of {common.money(v['cost_usd'])}.")
    if hints:
        lines += ["", "## Savings opportunities", "", *hints,
                  "", "_Same tier ≠ identical quality. Test on a sample before switching._"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="record actual spend")
    rec.add_argument("--model")
    rec.add_argument("--unit")
    rec.add_argument("--units", type=float, default=0)
    rec.add_argument("--input-tokens", type=int, default=0)
    rec.add_argument("--output-tokens", type=int, default=0)
    rec.add_argument("--cached-input-tokens", type=int, default=0)
    rec.add_argument("--batch", action="store_true")
    rec.add_argument("--cost", type=float, help="record an exact dollar amount instead")
    rec.add_argument("--estimated", action="store_true", help="numbers are estimates, not provider-reported")
    rec.add_argument("--note", default="")

    for name in ("summary", "report"):
        p = sub.add_parser(name)
        p.add_argument("--period", choices=["today", "week", "month", "all"], default="today")
        if name == "report":
            p.add_argument("--out", help="write markdown here instead of stdout")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "record":
            print(json.dumps(cmd_record(args), indent=2))
        elif args.cmd == "summary":
            print(json.dumps(summarize(args.period), indent=2))
        else:
            md = to_markdown(summarize(args.period))
            if args.out:
                Path(args.out).write_text(md, encoding="utf-8")
                print(json.dumps({"written": args.out}))
            else:
                print(md)
    except KeyError as exc:
        print(json.dumps({"error": str(exc).strip('"')}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
