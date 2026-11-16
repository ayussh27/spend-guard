#!/usr/bin/env python3
"""Estimate what a job will cost BEFORE running it, and check it against the budget.

Examples
  # 40k-token prompt, ~8k tokens out, 25 calls on Sonnet
  python estimate.py --model claude-sonnet-5-5 --input-tokens 40000 --output-tokens 8000 --calls 25

  # Count tokens from real files
  python estimate.py --model gpt-6.1-sol --input-file docs/*.md --output-tokens 2000

  # Same job priced on every model
  python estimate.py --compare --input-tokens 40000 --output-tokens 8000 --calls 25

  # Unit-priced tool (credits, images, video seconds)
  python estimate.py --unit example-video-credits --units 120

Exit codes: 0 = ok or warn, 2 = blocked by policy (so scripts can stop automatically).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def count_input(args, policy) -> int:
    if args.input_tokens is not None:
        return args.input_tokens
    text = args.input_text or ""
    for f in args.input_file or []:
        text += Path(f).read_text(encoding="utf-8", errors="ignore")
    return common.estimate_tokens(text)


def output_tokens(args, input_tokens: int, policy) -> int:
    if args.output_tokens is not None:
        return args.output_tokens
    ratio = float(policy["routing"].get("default_output_ratio", 0.5))
    return int(input_tokens * ratio)


def cheaper_alternatives(pricing, model, in_tok, out_tok, calls, batch, policy, limit=2):
    """Models in the SAME or HIGHER tier that are cheaper than `model`."""
    base = common.model_price(pricing, model)
    blocked = set(policy["routing"].get("blocked_models", []))
    allowed = set(policy["routing"].get("allowed_providers", []))
    current = common.token_cost(pricing, model, in_tok, out_tok, batch=batch) * calls
    alts = []
    for name, entry in pricing.get("models", {}).items():
        if name == model or name in blocked:
            continue
        if allowed and entry.get("provider") not in allowed:
            continue
        if entry.get("tier", 0) < base.get("tier", 0):
            continue
        cost = common.token_cost(pricing, name, in_tok, out_tok, batch=batch) * calls
        if cost < current:
            alts.append({"model": name, "cost_usd": round(cost, 6), "saves_usd": round(current - cost, 6)})
    return sorted(alts, key=lambda a: a["cost_usd"])[:limit]


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="model id from pricing.yaml")
    ap.add_argument("--compare", action="store_true", help="price the job on every model")
    ap.add_argument("--input-tokens", type=int)
    ap.add_argument("--input-text")
    ap.add_argument("--input-file", nargs="*")
    ap.add_argument("--output-tokens", type=int)
    ap.add_argument("--cached-input-tokens", type=int, default=0)
    ap.add_argument("--calls", type=int, default=1, help="how many times the job runs")
    ap.add_argument("--batch", action="store_true", help="apply batch-API discount")
    ap.add_argument("--unit", help="unit-priced item from pricing.yaml (credits, images, ...)")
    ap.add_argument("--units", type=float, default=0)
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args(argv)
    if not (args.model or args.compare or args.unit):
        ap.error("--model is required (or use --compare / --unit)")
    return args


def run(argv=None) -> dict:
    """Parse arguments and return the estimate as a dict (used by main() and the tests)."""
    return compute(parse(argv))


def compute(args) -> dict:
    pricing, policy = common.load_pricing(), common.load_policy()

    # ---- unit-priced tools
    if args.unit:
        item = pricing.get("units", {}).get(args.unit, {})
        cost = common.unit_cost(pricing, args.unit, args.units)
        result = {
            "kind": "units",
            "item": args.unit,
            "units": args.units,
            "unit": item.get("unit"),
            "cost_usd": round(cost, 6),
            "warnings": (["unit_cost is an EXAMPLE value — set your real price in pricing.yaml"]
                         if item.get("example") else []),
        }
        result.update(common.check_policy(cost, policy))
        return result

    in_tok = count_input(args, policy)
    out_tok = output_tokens(args, in_tok, policy)

    # ---- compare across all models
    if args.compare:
        rows = []
        for name, entry in pricing.get("models", {}).items():
            cost = common.token_cost(pricing, name, in_tok, out_tok, args.cached_input_tokens, args.batch) * args.calls
            rows.append({"model": name, "provider": entry.get("provider"), "tier": entry.get("tier"),
                         "cost_usd": round(cost, 6)})
        rows.sort(key=lambda r: r["cost_usd"])
        return {"kind": "compare", "input_tokens": in_tok, "output_tokens": out_tok,
                "calls": args.calls, "batch": args.batch, "models": rows}

    entry = common.model_price(pricing, args.model)
    per_call = common.token_cost(pricing, args.model, in_tok, out_tok, args.cached_input_tokens, args.batch)
    total = per_call * args.calls

    warnings = []
    if common.is_stale(entry, pricing):
        warnings.append(f"price for {args.model} last verified {entry.get('last_verified', 'never')} "
                        f"— check {entry.get('source', 'the provider pricing page')}")
    if args.input_tokens is None:
        warnings.append("input tokens are estimated (~4 chars/token unless tiktoken is installed)")
    lc = entry.get("long_context")
    if lc and in_tok > int(lc["above_tokens"]):
        warnings.append(f"prompt is over {int(lc['above_tokens']):,} tokens, so {args.model}'s "
                        "higher long-context prices apply to the whole request")
    if args.output_tokens is None:
        warnings.append("output tokens assumed from routing.default_output_ratio")

    result = {
        "kind": "tokens",
        "model": args.model,
        "provider": entry.get("provider"),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "calls": args.calls,
        "batch": args.batch,
        "cost_per_call_usd": round(per_call, 6),
        "cost_usd": round(total, 6),
        "cheaper_alternatives": cheaper_alternatives(pricing, args.model, in_tok, out_tok,
                                                     args.calls, args.batch, policy),
        "warnings": warnings,
    }
    result.update(common.check_policy(total, policy))
    return result


def to_text(r: dict) -> str:
    if r["kind"] == "compare":
        lines = [f"Job: {r['input_tokens']:,} in / {r['output_tokens']:,} out tokens x {r['calls']} call(s)"
                 + (" [batch]" if r["batch"] else ""), ""]
        for m in r["models"]:
            lines.append(f"  {m['model']:<24} tier {m['tier']}  {common.money(m['cost_usd']):>12}")
        return "\n".join(lines)
    head = (f"{r.get('model', r.get('item'))}: {common.money(r['cost_usd'])}"
            + (f" ({r['calls']} x {common.money(r['cost_per_call_usd'])})" if r.get("calls", 1) > 1 else ""))
    lines = [head, f"Verdict: {r['verdict'].upper()}"]
    lines += [f"  - {x}" for x in r["reasons"]]
    for alt in r.get("cheaper_alternatives", []):
        lines.append(f"  cheaper: {alt['model']} {common.money(alt['cost_usd'])} (saves {common.money(alt['saves_usd'])})")
    lines += [f"  ! {w}" for w in r.get("warnings", [])]
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse(argv)
    try:
        r = compute(args)
    except KeyError as exc:
        print(json.dumps({"error": str(exc).strip('"')}), file=sys.stderr)
        return 1
    print(to_text(r) if args.format == "text" else json.dumps(r, indent=2))
    return 2 if r.get("verdict") == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
