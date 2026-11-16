"""Shared helpers for agent-budget-guard: pricing, policy, ledger, token estimates."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
from typing import Any

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PRICING = SKILL_DIR / "pricing.yaml"
DEFAULT_POLICY = SKILL_DIR / "budget.yaml"
DEFAULT_LEDGER = Path.home() / ".budget-guard" / "ledger.jsonl"


# ---------------------------------------------------------------- loading

def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def pricing_path() -> Path:
    return Path(os.environ.get("BUDGET_GUARD_PRICING", DEFAULT_PRICING))


def policy_path() -> Path:
    """Env var wins, then ./budget.yaml in the current project, then the skill default."""
    env = os.environ.get("BUDGET_GUARD_CONFIG")
    if env:
        return Path(env)
    local = Path.cwd() / "budget.yaml"
    if local.exists():
        return local
    return DEFAULT_POLICY


def ledger_path() -> Path:
    return Path(os.environ.get("BUDGET_GUARD_LEDGER", DEFAULT_LEDGER))


def load_pricing() -> dict[str, Any]:
    return _read_yaml(pricing_path())


def load_policy() -> dict[str, Any]:
    policy = _read_yaml(policy_path())
    policy.setdefault("caps", {})
    policy.setdefault("routing", {})
    policy.setdefault("hooks", {})
    return policy


def today() -> dt.date:
    override = os.environ.get("BUDGET_GUARD_TODAY")  # for tests / demos
    return dt.date.fromisoformat(override) if override else dt.date.today()


# ---------------------------------------------------------------- prices

def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def model_price(pricing: dict[str, Any], model: str, on: dt.date | None = None) -> dict[str, Any]:
    """Return the effective price entry for `model` on date `on` (applies price_changes)."""
    models = pricing.get("models", {})
    if model not in models:
        known = ", ".join(sorted(models))
        raise KeyError(f"Unknown model '{model}'. Known models: {known}")
    entry = dict(models[model])
    on = on or today()
    for change in sorted(entry.get("price_changes", []), key=lambda c: _as_date(c["effective_from"])):
        if _as_date(change["effective_from"]) <= on:
            entry.update({k: v for k, v in change.items() if k != "effective_from"})
    return entry


def is_stale(entry: dict[str, Any], pricing: dict[str, Any], on: dt.date | None = None) -> bool:
    if "last_verified" not in entry:
        return True
    days = int(pricing.get("stale_after_days", 45))
    return ((on or today()) - _as_date(entry["last_verified"])).days > days


def token_cost(
    pricing: dict[str, Any],
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    batch: bool = False,
    on: dt.date | None = None,
) -> float:
    """Cost in USD for one call. Prices in pricing.yaml are per 1M tokens.

    If the model has a `long_context` tier and the prompt is longer than its
    `above_tokens`, the higher long-context prices apply to the whole request.
    """
    p = model_price(pricing, model, on)
    lc = p.get("long_context")
    if lc and input_tokens > int(lc["above_tokens"]):
        p = {**p, **{k: v for k, v in lc.items() if k != "above_tokens"}}
    cached = min(cached_input_tokens, input_tokens)
    fresh = input_tokens - cached
    cached_rate = p.get("cached_input", p["input"])
    cost = (fresh * p["input"] + cached * cached_rate + output_tokens * p["output"]) / 1_000_000
    if batch:
        cost *= float(pricing.get("batch_discount", 0.5))
    return cost


def unit_cost(pricing: dict[str, Any], item: str, units: float) -> float:
    entries = pricing.get("units", {})
    if item not in entries:
        raise KeyError(f"Unknown unit-priced item '{item}'. Known: {', '.join(sorted(entries))}")
    return float(entries[item]["unit_cost"]) * units


# ---------------------------------------------------------------- tokens

def estimate_tokens(text: str) -> int:
    """Token count. Uses tiktoken when installed, else the ~4 chars/token rule of thumb.

    Different providers tokenize differently, so treat this as an estimate either way.
    """
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return math.ceil(len(text) / 4)


# ---------------------------------------------------------------- ledger

def read_ledger(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or ledger_path()
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip a corrupted line rather than crash the agent
    return rows


def append_ledger(row: dict[str, Any], path: Path | None = None) -> None:
    path = path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def spent_since(start: dt.date, rows: list[dict[str, Any]] | None = None) -> float:
    rows = read_ledger() if rows is None else rows
    total = 0.0
    for r in rows:
        try:
            if dt.date.fromisoformat(r["date"]) >= start:
                total += float(r.get("cost_usd", 0))
        except (KeyError, ValueError):
            continue
    return total


def spent_today(rows: list[dict[str, Any]] | None = None) -> float:
    return spent_since(today(), rows)


def spent_this_month(rows: list[dict[str, Any]] | None = None) -> float:
    return spent_since(today().replace(day=1), rows)


# ---------------------------------------------------------------- policy

def check_policy(cost: float, policy: dict[str, Any], rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compare an estimated cost with the caps. Returns verdict ok | warn | block plus reasons."""
    caps = policy.get("caps", {})
    warn_frac = float(caps.get("warn_at_fraction", 0.8))
    rows = read_ledger() if rows is None else rows
    day, month = spent_today(rows), spent_this_month(rows)

    verdict, reasons = "ok", []

    def bump(level: str, msg: str) -> None:
        nonlocal verdict
        order = {"ok": 0, "warn": 1, "block": 2}
        if order[level] > order[verdict]:
            verdict = level
        reasons.append(msg)

    checks = [
        ("per_task_usd", "per-task cap", 0.0),
        ("daily_usd", "daily cap", day),
        ("monthly_usd", "monthly cap", month),
    ]
    for key, label, already in checks:
        cap = caps.get(key)
        if cap is None:
            continue
        cap = float(cap)
        after = already + cost
        if already >= cap and key != "per_task_usd":
            bump("block", f"{label} already reached: ${already:.2f} of ${cap:.2f} spent")
        elif after > cap:
            bump("block", f"would exceed {label}: ${after:.2f} > ${cap:.2f}")
        elif after >= warn_frac * cap:
            bump("warn", f"near {label}: ${after:.2f} of ${cap:.2f} ({after / cap:.0%})")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "spent_today_usd": round(day, 4),
        "spent_month_usd": round(month, 4),
        "caps": {k: caps.get(k) for k in ("per_task_usd", "daily_usd", "monthly_usd")},
    }


def money(x: float) -> str:
    """Readable dollars: more decimals for tiny amounts."""
    if x == 0:
        return "$0.00"
    if abs(x) < 0.01:
        return f"${x:.5f}"
    return f"${x:,.2f}"
