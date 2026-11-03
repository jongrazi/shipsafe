"""The decision rules, which the model can't override."""
from __future__ import annotations

import pytest

from app import policy
from app.signals import Signal
from tests.helpers import make_order

CLEAN = [Signal("seller_protection", "ok", "")]
ONE_WATCH = [Signal("country", "watch", "")]
TWO_WATCHES = [Signal("country", "watch", ""), Signal("name", "watch", "")]
ONE_RISK = [Signal("past_disputes", "risk", "")]
TWO_RISKS = [Signal("past_disputes", "risk", ""), Signal("order_value", "risk", "")]


@pytest.mark.parametrize("signals, expected", [
    (CLEAN, "low"), (ONE_WATCH, "low"), (TWO_WATCHES, "medium"),
    (ONE_RISK, "medium"), (TWO_RISKS, "high"),
])
def test_rule_band(signals, expected):
    assert policy.rule_band(signals) == expected


def test_protection_shrinks_the_money_at_risk():
    assert policy.money_at_risk(make_order(amount_cents=10000, seller_protection="ELIGIBLE")) == 2000
    assert policy.money_at_risk(make_order(amount_cents=10000, seller_protection="PARTIALLY_ELIGIBLE")) == 6000
    assert policy.money_at_risk(make_order(amount_cents=10000, seller_protection="NOT_ELIGIBLE")) == 10000
    assert policy.money_at_risk(make_order(amount_cents=10000, seller_protection="")) == 10000


def test_model_can_raise_the_band():
    decision = policy.decide(make_order(), CLEAN, "high")
    assert decision.band == "high"


def test_model_cannot_lower_the_band():
    decision = policy.decide(make_order(), TWO_RISKS, "low")
    assert (decision.band, decision.rule_band, decision.model_band) == ("high", "high", "low")


def test_persuaded_model_cannot_ship_a_risky_expensive_order():
    order = make_order(amount_cents=40000, seller_protection="NOT_ELIGIBLE")
    assert policy.decide(order, TWO_RISKS, "low").action == "hold"


def test_hold_threshold_follows_the_formula():
    order = make_order(amount_cents=14900, seller_protection="ELIGIBLE")
    decision = policy.decide(order, CLEAN, "low")
    assert decision.money_at_risk_cents == 2980
    assert decision.hold_above == pytest.approx(500 / (2980 + 500))


def test_clean_order_ships():
    assert policy.decide(make_order(), CLEAN, "low").action == "ship"


def test_cheap_order_with_a_flag_ships():
    order = make_order(amount_cents=1500, seller_protection="NOT_ELIGIBLE")
    assert policy.decide(order, ONE_RISK, "medium").action == "ship"


def test_same_flag_on_an_expensive_order_goes_to_the_buyer():
    order = make_order(amount_cents=40000, seller_protection="NOT_ELIGIBLE")
    assert policy.decide(order, ONE_RISK, "medium").action == "contact_buyer"


def test_high_band_is_held():
    order = make_order(amount_cents=40000, seller_protection="ELIGIBLE")
    assert policy.decide(order, CLEAN, "high").action == "hold"


def test_failed_review_holds_even_a_clean_cheap_order():
    decision = policy.decide(make_order(amount_cents=500), CLEAN, None)
    assert (decision.action, decision.model_band) == ("hold", None)
    assert "seller" in decision.reason


def test_demo_items_on_the_shop_history(tmp_path):
    """The demo relies on this: a clean buyer's backpack ships, the same buyer's tent is checked first."""
    import json

    from app import config, main, signals
    from app.store import Store

    store = Store(tmp_path / "shop.db")
    now = 1_800_000_000.0
    store.seed(json.loads(config.SHOP_HISTORY.read_text(encoding="utf-8")), now=now)
    for sku, expected in (("DEMO-001", "ship"), ("DEMO-002", "contact_buyer")):
        cents = round(float(main.ITEMS[sku]["price"]) * 100)
        order = make_order(amount_cents=cents, created_at=now)
        assert policy.decide(order, signals.build(order, store), "low").action == expected
