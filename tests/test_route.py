import common
import route


def test_classifier_tiers():
    assert route.classify("classify these emails by topic")[0] == 1
    assert route.classify("extract invoice numbers from PDFs")[0] == 1
    assert route.classify("design the architecture for a payments platform")[0] == 3
    assert route.classify("write a blog post about cats")[0] == 2


def test_recommends_cheapest_model_meeting_tier():
    r = route.run(["--tier", "2", "--input-tokens", "10000", "--output-tokens", "1000"])
    pricing = common.load_pricing()
    assert pricing["models"][r["recommended"]["model"]]["tier"] >= 2
    for alt in r["alternatives"]:
        assert alt["cost_usd"] >= r["recommended"]["cost_usd"]


def test_never_routes_below_required_tier():
    r = route.run(["--tier", "3", "--input-tokens", "1000", "--output-tokens", "100"])
    pricing = common.load_pricing()
    for m in [r["recommended"], *r["alternatives"]]:
        assert pricing["models"][m["model"]]["tier"] == 3


def test_provider_filter():
    r = route.run(["--tier", "1", "--provider", "anthropic", "--input-tokens", "1000"])
    assert r["recommended"]["provider"] == "anthropic"


def test_policy_blocked_models_and_allowed_providers(write_policy):
    write_policy("routing:\n  allowed_providers: [anthropic, google]\n  blocked_models: [claude-haiku-5-5]\n")
    r = route.run(["--tier", "1", "--input-tokens", "1000", "--output-tokens", "100"])
    names = [r["recommended"]["model"], *[a["model"] for a in r["alternatives"]]]
    assert "claude-haiku-5-5" not in names
    assert all(not n.startswith("gpt") for n in names)


def test_reports_savings():
    r = route.run(["--tier", "1", "--input-tokens", "100000", "--output-tokens", "10000", "--calls", "10"])
    assert r["saves_vs_priciest_eligible_usd"] > 0
