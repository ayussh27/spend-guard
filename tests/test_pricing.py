import datetime as dt

import pytest

import common


def test_every_model_entry_is_complete():
    pricing = common.load_pricing()
    assert pricing["models"], "pricing.yaml has no models"
    for name, e in pricing["models"].items():
        assert e["provider"], name
        assert e["tier"] in (1, 2, 3), name
        assert e["input"] >= 0 and e["output"] >= 0, name
        assert e["source"].startswith("https://"), name
        dt.date.fromisoformat(str(e["last_verified"]))


def test_token_cost_math():
    pricing = common.load_pricing()
    # Sonnet 5.5: $2 in / $10 out per 1M -> 1M in + 1M out = $12
    assert common.token_cost(pricing, "claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    # 40k in, 8k out -> 0.08 + 0.08
    assert common.token_cost(pricing, "claude-sonnet-5-5", 40_000, 8_000) == pytest.approx(0.16)


def test_batch_discount_halves_cost():
    pricing = common.load_pricing()
    full = common.token_cost(pricing, "gpt-6.1-sol", 100_000, 10_000)
    batch = common.token_cost(pricing, "gpt-6.1-sol", 100_000, 10_000, batch=True)
    assert batch == pytest.approx(full / 2)


def test_cached_input_is_cheaper():
    pricing = common.load_pricing()
    fresh = common.token_cost(pricing, "claude-opus-5-5", 100_000, 0)
    cached = common.token_cost(pricing, "claude-opus-5-5", 100_000, 0, cached_input_tokens=100_000)
    assert cached < fresh
    assert cached == pytest.approx(100_000 * 0.20 / 1_000_000)


def test_scheduled_price_change_applies_on_its_date():
    pricing = {"models": {"m": {
        "provider": "x", "tier": 2, "input": 2.00, "output": 10.00,
        "price_changes": [{"effective_from": "2026-12-01", "input": 4.00, "output": 20.00}],
    }}}
    before = common.model_price(pricing, "m", dt.date(2026, 11, 30))
    after = common.model_price(pricing, "m", dt.date(2026, 12, 1))
    assert (before["input"], before["output"]) == (2.00, 10.00)
    assert (after["input"], after["output"]) == (4.00, 20.00)


def test_long_context_prices_apply_above_threshold():
    pricing = common.load_pricing()
    # Haiku 5.5: $0.10/$0.50 up to 100k input tokens, $0.50/$2.50 above
    short = common.token_cost(pricing, "claude-haiku-5-5", 100_000, 1_000_000)
    long = common.token_cost(pricing, "claude-haiku-5-5", 100_001, 1_000_000)
    assert short == pytest.approx(0.01 + 0.50)
    assert long == pytest.approx(100_001 * 0.50 / 1_000_000 + 2.50)


def test_long_context_warning_in_estimate():
    import estimate
    r = estimate.run(["--model", "claude-haiku-5-5", "--input-tokens", "150000", "--output-tokens", "100"])
    assert any("long-context" in w for w in r["warnings"])


def test_unknown_model_raises_helpful_error():
    with pytest.raises(KeyError, match="Known models"):
        common.model_price(common.load_pricing(), "not-a-model")


def test_staleness():
    pricing = common.load_pricing()
    entry = {"last_verified": "2026-01-01"}
    assert common.is_stale(entry, pricing, dt.date(2026, 11, 16))
    assert not common.is_stale({"last_verified": "2026-11-10"}, pricing, dt.date(2026, 11, 16))
    assert common.is_stale({}, pricing)


def test_token_estimate_rule_of_thumb():
    assert common.estimate_tokens("") == 0
    n = common.estimate_tokens("a" * 400)
    assert 50 <= n <= 400  # ~100 with the 4-chars rule; tiktoken may differ
