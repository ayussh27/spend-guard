import pytest

import common
import estimate


def test_small_job_is_ok():
    r = estimate.run(["--model", "claude-haiku-5-5", "--input-tokens", "2000", "--output-tokens", "200"])
    assert r["verdict"] == "ok"
    assert r["cost_usd"] == pytest.approx((2000 * 0.10 + 200 * 0.50) / 1_000_000)


def test_over_per_task_cap_is_blocked():
    r = estimate.run(["--model", "claude-opus-5-5", "--input-tokens", "40000",
                      "--output-tokens", "8000", "--calls", "25"])
    assert r["cost_usd"] == pytest.approx(8.0)
    assert r["verdict"] == "block"
    assert any("per-task cap" in x for x in r["reasons"])


def test_block_returns_exit_code_2(capsys):
    code = estimate.main(["--model", "claude-opus-5-5", "--input-tokens", "40000",
                          "--output-tokens", "8000", "--calls", "25"])
    assert code == 2


def test_cheaper_alternatives_stay_in_same_or_higher_tier():
    r = estimate.run(["--model", "claude-opus-5-5", "--input-tokens", "40000", "--output-tokens", "8000"])
    pricing = common.load_pricing()
    for alt in r["cheaper_alternatives"]:
        assert pricing["models"][alt["model"]]["tier"] >= 3
        assert alt["cost_usd"] < r["cost_usd"]


def test_warn_near_cap(write_policy):
    write_policy("caps:\n  per_task_usd: 1.00\n  warn_at_fraction: 0.8\n")
    # Sonnet: 450k in = $0.90 -> 90% of $1 cap
    r = estimate.run(["--model", "claude-sonnet-5-5", "--input-tokens", "450000", "--output-tokens", "0"])
    assert r["verdict"] == "warn"


def test_daily_spend_already_at_cap_blocks_everything(write_policy):
    write_policy("caps:\n  daily_usd: 1.00\n")
    common.append_ledger({"date": "2026-11-16", "model": "x", "cost_usd": 1.0})
    r = estimate.run(["--model", "claude-haiku-5-5", "--input-tokens", "10", "--output-tokens", "10"])
    assert r["verdict"] == "block"
    assert any("already reached" in x for x in r["reasons"])


def test_yesterdays_spend_does_not_count_today(write_policy):
    write_policy("caps:\n  daily_usd: 1.00\n")
    common.append_ledger({"date": "2026-11-15", "model": "x", "cost_usd": 5.0})
    r = estimate.run(["--model", "claude-haiku-5-5", "--input-tokens", "10", "--output-tokens", "10"])
    assert r["verdict"] == "ok"


def test_input_file_counts_tokens(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("word " * 1000)
    r = estimate.run(["--model", "claude-sonnet-5-5", "--input-file", str(f), "--output-tokens", "0"])
    assert r["input_tokens"] > 0
    assert any("estimated" in w for w in r["warnings"])


def test_compare_is_sorted_cheapest_first():
    r = estimate.run(["--compare", "--input-tokens", "10000", "--output-tokens", "1000"])
    costs = [m["cost_usd"] for m in r["models"]]
    assert costs == sorted(costs)
    assert len(r["models"]) == len(common.load_pricing()["models"])


def test_unit_priced_item_flags_example_price():
    r = estimate.run(["--unit", "example-video-credits", "--units", "10"])
    assert r["cost_usd"] == pytest.approx(0.5)
    assert r["warnings"]
