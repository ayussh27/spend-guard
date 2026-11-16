---
name: agent-budget-guard
description: Estimate LLM/API/AI-tool costs before running expensive jobs, enforce spending caps, pick the cheapest model that is good enough, and log actual spend. Use before batch LLM calls, large-context prompts, image/video/audio generation, or any loop that calls a paid API — and when the user asks what something will cost or how much they've spent.
---

# Agent Budget Guard

You are spending the user's money every time you call a paid model or tool. This skill
makes that visible and controlled: **estimate → check the cap → choose the cheapest
good-enough model → run → record what it actually cost.**

All scripts live in `scripts/` next to this file and print JSON. Run them with `python3`.

## When to use it

Run an estimate **before** any of these:
- A job that calls an LLM API more than ~10 times (batches, loops, per-file processing)
- A single prompt over ~50,000 tokens
- Any image, video, audio, or other credit-based generation
- Anything the user describes as "for all of…", "every…", "each of the…"

Also use it when the user asks "how much will this cost?", "which model should I use?",
or "how much have I spent?".

Skip it for one-off small questions — don't add friction to cheap work.

## Workflow

### 1. Pick the model (if the user hasn't)

Judge the task's difficulty yourself and pass the tier explicitly:
- **Tier 1 — simple:** classify, extract, tag, format, translate, short summaries
- **Tier 2 — standard:** most coding, writing, analysis, RAG answers
- **Tier 3 — complex:** hard reasoning, architecture, research synthesis, long agent runs

```bash
python3 scripts/route.py --tier 1 --input-tokens 3000 --output-tokens 200 --calls 500
```

If you're unsure, pass `--task "<description>"` and let the keyword classifier guess,
then sanity-check its `tier_reason`. If the user named a model, respect it — but mention
`cheaper_alternatives` from the estimate if they're significant.

### 2. Estimate the cost

```bash
# token-priced models
python3 scripts/estimate.py --model claude-sonnet-5-5 --input-tokens 40000 --output-tokens 8000 --calls 25
# count tokens from the actual files you will send
python3 scripts/estimate.py --model gpt-6.1-sol --input-file path/to/*.md --output-tokens 2000
# credit/unit-priced tools
python3 scripts/estimate.py --unit example-video-credits --units 120
# show the same job on every model
python3 scripts/estimate.py --compare --input-tokens 40000 --output-tokens 8000 --calls 25
```

Use `--batch` when the work can go through a provider's batch API (usually 50% off) —
and suggest it to the user when the job isn't time-sensitive.

### 3. Act on the verdict

- **`ok`** → proceed. Mention the estimate in one line if the job is large.
- **`warn`** → tell the user the estimate and which cap it approaches, then proceed
  unless they object.
- **`block`** (exit code 2) → **stop. Do not run the job.** Show the user the estimate,
  the reasons, and any cheaper alternatives. Continue only if the user explicitly
  approves or raises the cap in `budget.yaml`. Never edit the caps yourself.

Read `warnings` too: stale prices, estimated token counts, or example unit prices mean
the number is rougher than it looks — say so.

### 4. Record actual spend afterwards

Use the real usage numbers the API returned (most APIs return `usage` with input/output
token counts):

```bash
python3 scripts/ledger.py record --model claude-sonnet-5-5 --input-tokens 41230 --output-tokens 7902 --note "summarize docs"
```

If you have no real numbers, record the estimate with `--estimated` so reports stay honest.

### 5. Reporting

```bash
python3 scripts/ledger.py summary --period today          # JSON
python3 scripts/ledger.py report --period month --out spend-report.md   # markdown
```

When presenting spend, lead with the total vs the cap, then the biggest cost driver,
then any savings opportunity the report lists.

## Rules

- Never run a job that got a `block` verdict without the user's explicit go-ahead.
- Never change `budget.yaml` caps on your own.
- Don't present estimates as exact — token counts and prices are approximations.
- If a model isn't in `pricing.yaml`, say so and ask the user for its price (or suggest
  they add it) rather than guessing.

## Files

- `pricing.yaml` — per-model prices (USD per 1M tokens), tiers, sources, verification dates
- `budget.yaml` — caps, routing filters, hook settings (copy to the project root to override)
- `hooks/pre_tool_use.py` — optional Claude Code hook that hard-blocks expensive tools once
  the daily or monthly cap is reached
