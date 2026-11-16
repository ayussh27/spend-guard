import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent / "skills" / "agent-budget-guard"
sys.path.insert(0, str(SKILL / "scripts"))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own empty ledger, a fixed 'today', and the default policy."""
    monkeypatch.setenv("BUDGET_GUARD_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("BUDGET_GUARD_TODAY", "2026-11-16")
    monkeypatch.setenv("BUDGET_GUARD_CONFIG", str(SKILL / "budget.yaml"))
    monkeypatch.delenv("BUDGET_GUARD_PRICING", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def skill_dir():
    return SKILL


@pytest.fixture
def write_policy(tmp_path, monkeypatch):
    """Write a custom budget.yaml for one test."""
    def _write(text: str) -> Path:
        p = tmp_path / "custom-budget.yaml"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv("BUDGET_GUARD_CONFIG", str(p))
        return p
    return _write
