#!/usr/bin/env python3
"""Pick the cheapest model that is good enough for a task.

The agent should pass --tier itself when it can judge the task (1 simple, 2 standard,
3 complex). Without --tier, a small keyword classifier guesses from --task.

Examples
  python route.py --tier 1 --input-tokens 3000 --output-tokens 200 --calls 500
  python route.py --task "classify 500 support emails by topic" --calls 500
  python route.py --task "design the architecture for a multi-tenant billing system" --provider anthropic
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

SIMPLE = [
    r"\bclassif", r"\bcategori", r"\blabel", r"\btag(ging)?\b", r"\bextract", r"\bformat",
    r"\bconvert", r"\btranslat", r"\brename", r"\btypo", r"\bspell", r"\bsentiment",
    r"\bshort summar", r"\btl;?dr", r"\byes/no\b", r"\bregex\b", r"\bparse\b", r"\bdedupe",
]
COMPLEX = [
    r"\barchitect", r"\bdesign (a|the) system", r"\bprove\b", r"\bproof\b", r"\bresearch",
    r"\bstrateg", r"\bsecurity (audit|review)", r"\bthreat model", r"\bmulti-step",
    r"\blong[- ]horizon", r"\bnovel\b", r"\brefactor (the|a|entire) (codebase|repo)",
    r"\bdebug (a )?(complex|hard|race|concurrency)", r"\blegal\b", r"\bmedical\b",
    r"\bplan (the|a) migration",
]


def _label(pattern: str) -> str:
    return re.sub(r"\\b|\(|\)|\?|\||\\", "", pattern)


def classify(task: str) -> tuple[int, str]:
    t = task.lower()
    c = [p for p in COMPLEX if re.search(p, t)]
    s = [p for p in SIMPLE if re.search(p, t)]
    if c:
        return 3, f"complex signals: {', '.join(_label(x) for x in c[:3])}"
    if s:
        return 1, f"simple signals: {', '.join(_label(x) for x in s[:3])}"
    return 2, "no strong signals — defaulting to standard tier"


def candidates(pricing, policy, min_tier, provider=None):
    blocked = set(policy["routing"].get("blocked_models", []))
    allowed = set(policy["routing"].get("allowed_providers", []))
    for name, entry in pricing.get("models", {}).items():
        if name in blocked or entry.get("tier", 0) < min_tier:
            continue
        if allowed and entry.get("provider") not in allowed:
            continue
        if provider and entry.get("provider") != provider:
            continue
        yield name, entry


def run(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", default="", help="plain-English description of the job")
    ap.add_argument("--tier", type=int, choices=[1, 2, 3], help="override the classifier")
    ap.add_argument("--provider", help="only consider one provider (anthropic, openai, google, ...)")
    ap.add_argument("--input-tokens", type=int)
    ap.add_argument("--output-tokens", type=int)
    ap.add_argument("--calls", type=int, default=1)
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--top", type=int, default=3, help="how many alternatives to list")
    args = ap.parse_args(argv)

    pricing, policy = common.load_pricing(), common.load_policy()

    if args.tier:
        tier, why = args.tier, "tier set by caller"
    elif args.task:
        tier, why = classify(args.task)
    else:
        ap.error("give --tier or --task")

    in_tok = args.input_tokens if args.input_tokens is not None else max(common.estimate_tokens(args.task), 1000)
    ratio = float(policy["routing"].get("default_output_ratio", 0.5))
    out_tok = args.output_tokens if args.output_tokens is not None else int(in_tok * ratio)

    ranked = []
    for name, entry in candidates(pricing, policy, tier, args.provider):
        cost = common.token_cost(pricing, name, in_tok, out_tok, batch=args.batch) * args.calls
        ranked.append({"model": name, "provider": entry["provider"], "tier": entry["tier"],
                       "cost_usd": round(cost, 6)})
    ranked.sort(key=lambda r: (r["cost_usd"], r["tier"]))

    if not ranked:
        return {"error": f"no model satisfies tier>={tier} with current filters", "tier": tier}

    best = ranked[0]
    priciest = max(ranked, key=lambda r: r["cost_usd"])
    result = {
        "task": args.task,
        "tier": tier,
        "tier_reason": why,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "calls": args.calls,
        "recommended": best,
        "alternatives": ranked[1 : 1 + args.top],
        "saves_vs_priciest_eligible_usd": round(priciest["cost_usd"] - best["cost_usd"], 6),
        "priciest_eligible": priciest["model"],
    }
    result.update(common.check_policy(best["cost_usd"], policy))
    return result


def main(argv=None) -> int:
    r = run(argv)
    print(json.dumps(r, indent=2))
    if "error" in r:
        return 1
    return 2 if r.get("verdict") == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
