import json
import os
import subprocess
import sys

import pytest

import common
import ledger


def test_record_and_summary():
    ledger.main(["record", "--model", "claude-sonnet-5-5", "--input-tokens", "40000",
                 "--output-tokens", "8000", "--note", "job A"])
    ledger.main(["record", "--cost", "0.50", "--note", "manual"])
    s = ledger.summarize("today")
    assert s["entries"] == 2
    assert s["total_usd"] == pytest.approx(0.66)
    assert list(s["by_model"])[0] == "manual"  # sorted by cost, biggest first


def test_estimated_flag_is_kept():
    ledger.main(["record", "--model", "gpt-6.1-sol", "--input-tokens", "100", "--estimated"])
    assert common.read_ledger()[0]["estimated"] is True
    assert ledger.summarize("today")["estimated_entries"] == 1


def test_corrupt_ledger_line_is_skipped():
    path = common.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"date": "2026-11-16", "model": "x", "cost_usd": 1}\nnot json\n')
    assert len(common.read_ledger()) == 1


def test_markdown_report(tmp_path):
    # two jobs of 150k input each: priced separately they stay under Gemini Pro's 200k
    # long-context threshold, so a cheaper tier-3 option exists
    for note in ("run 1", "run 2"):
        ledger.main(["record", "--model", "claude-opus-5-5", "--input-tokens", "150000",
                     "--output-tokens", "50000", "--note", note])
    out = tmp_path / "report.md"
    ledger.main(["report", "--period", "month", "--out", str(out)])
    md = out.read_text()
    assert "# Agent spend report" in md
    assert "claude-opus-5-5" in md
    assert "Savings opportunities" in md  # a cheaper tier-3 model exists


# ---------------------------------------------------------------- hook

def run_hook(skill_dir, event, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, str(skill_dir / "hooks" / "pre_tool_use.py")],
                          input=json.dumps(event), capture_output=True, text=True, env=env)


def test_hook_allows_ordinary_tools_even_over_cap(skill_dir, write_policy):
    write_policy("caps:\n  daily_usd: 1.00\nhooks:\n  watch_tools: ['generate_video']\n")
    common.append_ledger({"date": "2026-11-16", "model": "x", "cost_usd": 5.0})
    p = run_hook(skill_dir, {"tool_name": "Read", "tool_input": {"file_path": "a.txt"}})
    assert p.returncode == 0


def test_hook_blocks_watched_tool_over_cap(skill_dir, write_policy):
    write_policy("caps:\n  daily_usd: 1.00\nhooks:\n  watch_tools: ['generate_video']\n")
    common.append_ledger({"date": "2026-11-16", "model": "x", "cost_usd": 1.0})
    p = run_hook(skill_dir, {"tool_name": "mcp__studio__generate_video", "tool_input": {}})
    assert p.returncode == 2
    assert "budget reached" in p.stderr


def test_hook_allows_watched_tool_under_cap(skill_dir, write_policy):
    write_policy("caps:\n  daily_usd: 10.00\nhooks:\n  watch_tools: ['generate_video']\n")
    common.append_ledger({"date": "2026-11-16", "model": "x", "cost_usd": 1.0})
    p = run_hook(skill_dir, {"tool_name": "mcp__studio__generate_video", "tool_input": {}})
    assert p.returncode == 0


def test_hook_watches_bash_commands(skill_dir, write_policy):
    write_policy("caps:\n  daily_usd: 1.00\nhooks:\n  watch_bash: ['render_video']\n")
    common.append_ledger({"date": "2026-11-16", "model": "x", "cost_usd": 2.0})
    blocked = run_hook(skill_dir, {"tool_name": "Bash", "tool_input": {"command": "python render_video.py"}})
    allowed = run_hook(skill_dir, {"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    assert blocked.returncode == 2
    assert allowed.returncode == 0


def test_hook_survives_bad_input(skill_dir):
    p = subprocess.run([sys.executable, str(skill_dir / "hooks" / "pre_tool_use.py")],
                       input="not json", capture_output=True, text=True)
    assert p.returncode == 0
