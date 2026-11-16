# spend-guard 💸🛡️

**Cost control for AI agents.** Skills that let Claude Code, Codex, Cursor and other
agents know what they're about to spend, and stop before they overspend.

> AI agents can now run 500 API calls, render videos and process whole folders on their own.
> Nobody tells them what that costs. spend-guard does.

| Skill | Status | What it does |
|---|---|---|
| [`agent-budget-guard`](skills/agent-budget-guard) | ✅ v0.1 | Estimates LLM/API/credit costs before a job runs, enforces spending caps, routes to the cheapest good-enough model, logs actual spend |
| `aws-waste-audit` | 🚧 planned | Read-only audit of an AWS account for wasted spend |

---

## agent-budget-guard

Before an expensive job, the agent runs an estimate:

```text
$ python3 scripts/estimate.py --model claude-opus-5-5 --input-tokens 40000 --output-tokens 8000 --calls 25 --format text

claude-opus-5-5: $8.00 (25 x $0.32)
Verdict: BLOCK
  - would exceed per-task cap: $8.00 > $2.00
  - near daily cap: $8.00 of $10.00 (80%)
  cheaper: gemini-4-argon $4.00 (saves $4.00)
```

A `block` verdict means the agent stops and asks you first. `warn` means it tells you and
carries on. `ok` means it just runs.

### What it does

- **Estimate.** Prices token jobs (input, output, cached input, batch discount) and
  unit-priced tools (video credits, images). It can count tokens straight from the files
  you're about to send.
- **Enforce.** Per-task, daily and monthly caps from a simple `budget.yaml`.
- **Route.** Picks the cheapest model in the capability tier the task needs (1 simple,
  2 standard, 3 complex), across Anthropic, OpenAI and Google.
- **Log.** A local JSONL ledger of what was actually spent, plus markdown reports with
  savings opportunities ([sample](examples/sample-report.md)).
- **Hard stop (optional).** A Claude Code `PreToolUse` hook blocks expensive tools once
  you've hit your daily or monthly cap, even if the agent forgets to check.

### Design choices

- **The agent judges difficulty, and the script does the math.** `SKILL.md` tells the agent
  to pick the tier. Python handles the pricing, caps and logging, so the numbers are
  deterministic and testable.
- **Caps belong to the human.** The skill tells the agent never to edit `budget.yaml` itself.
- **Honest about uncertainty.** Estimates flag stale prices, guessed token counts and
  placeholder unit prices. Ledger entries without provider-reported usage are marked
  `estimated`.
- **Scheduled price changes.** `pricing.yaml` supports `price_changes` with an
  `effective_from` date, so a known price increase applies automatically on the right day.
- **No lock-in.** Plain YAML, a JSONL ledger and standard-library Python plus PyYAML.

---

## Install

**Claude Code (or any agent that supports the Agent Skills format):**

```bash
git clone https://github.com/<your-username>/spend-guard.git
cp -r spend-guard/skills/agent-budget-guard ~/.claude/skills/
pip install pyyaml
```

Or paste the repo link into your agent and say *"install the agent-budget-guard skill from this repo."*

For Codex, Cursor and others, copy the folder into that tool's skills directory.

**Optional hard stop:** merge [`hooks/settings.example.json`](skills/agent-budget-guard/hooks/settings.example.json)
into `~/.claude/settings.json`. Then set which tools count as expensive in
`budget.yaml` → `hooks.watch_tools`.

## Configure

Copy `budget.yaml` into your project root (or point `BUDGET_GUARD_CONFIG` at it):

```yaml
caps:
  per_task_usd: 2.00
  daily_usd: 10.00
  monthly_usd: 100.00
  warn_at_fraction: 0.8
routing:
  allowed_providers: [anthropic, google]   # empty = all
  blocked_models: []
```

| Env var | Default |
|---|---|
| `BUDGET_GUARD_CONFIG` | `./budget.yaml`, then the skill's default |
| `BUDGET_GUARD_PRICING` | the skill's `pricing.yaml` |
| `BUDGET_GUARD_LEDGER` | `~/.budget-guard/ledger.jsonl` |

## Use it directly (no agent needed)

```bash
cd skills/agent-budget-guard
python3 scripts/estimate.py --compare --input-tokens 40000 --output-tokens 8000 --calls 25 --format text
python3 scripts/route.py --task "classify 500 support emails by topic" --calls 500
python3 scripts/ledger.py record --model claude-sonnet-5-5 --input-tokens 41230 --output-tokens 7902
python3 scripts/ledger.py report --period month --out spend-report.md
```

## Prices

Prices live in [`pricing.yaml`](skills/agent-budget-guard/pricing.yaml), in USD per 1M tokens.
Each entry has a `source` and a `last_verified` date, and the estimator warns when an entry
is more than 45 days old. **Prices change often, so check them before relying on them.**
PRs that update prices are very welcome.

## Tests

```bash
pip install pyyaml pytest
pytest -q
```

The tests cover cost math, batch and caching discounts, scheduled price changes, cap
verdicts, routing filters, the ledger and the Claude Code hook. They use an isolated
temporary ledger, so they never touch your real spend data.

## Roadmap

- [ ] `aws-waste-audit` skill (read-only)
- [ ] Price-refresh helper that flags drift from official pricing pages
- [ ] Per-project budgets and tags in the ledger
- [ ] Optional quality check before routing a recurring job to a cheaper model

## Contributing

Issues and PRs are welcome, especially price updates, new providers, and better tier
assignments. Please include the `source` URL and today's date as `last_verified`.

## License

MIT
