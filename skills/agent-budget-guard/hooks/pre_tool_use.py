#!/usr/bin/env python3
"""Claude Code PreToolUse hook — hard stop for expensive tools once the daily cap is reached.

Claude Code sends the pending tool call as JSON on stdin (tool_name, tool_input, ...).
Exit code 2 blocks the call and shows our stderr message to Claude; exit 0 lets it run.

Which tools count as "expensive" is set in budget.yaml under `hooks.watch_tools`
(tool-name regexes) and `hooks.watch_bash` (regexes matched against Bash commands).

Install: see hooks/settings.example.json. This hook never blocks ordinary tools.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402


def is_watched(event: dict, hooks_cfg: dict) -> bool:
    tool = event.get("tool_name", "")
    if any(re.search(p, tool) for p in hooks_cfg.get("watch_tools", [])):
        return True
    if tool == "Bash":
        cmd = (event.get("tool_input") or {}).get("command", "")
        return any(re.search(p, cmd) for p in hooks_cfg.get("watch_bash", []))
    return False


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # never break the agent because of a malformed event

    policy = common.load_policy()
    if not is_watched(event, policy.get("hooks", {})):
        return 0

    caps = policy.get("caps", {})
    rows = common.read_ledger()
    checks = [("daily", caps.get("daily_usd"), common.spent_today(rows)),
              ("monthly", caps.get("monthly_usd"), common.spent_this_month(rows))]
    for label, cap, spent in checks:
        if cap is not None and spent >= float(cap):
            print(
                f"agent-budget-guard: {label} budget reached ({common.money(spent)} of "
                f"{common.money(float(cap))}). '{event.get('tool_name')}' is marked as expensive and was "
                "blocked. Tell the user, and only continue if they raise the cap in budget.yaml.",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
