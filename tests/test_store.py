"""The order store and the synthetic shop history."""
from __future__ import annotations

import json

import pytest

from app import config
from app.store import Store
from scripts.make_shop_history import build_rows
from tests.helpers import BASE_ORDER, NOW, make_order, other_buyer


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def test_order_survives_a_round_trip(store):
    assert store.add(BASE_ORDER)
    assert store.get(BASE_ORDER.order_id) == BASE_ORDER


def test_same_order_is_stored_once(store):
    assert store.add(BASE_ORDER)
    assert not store.add(BASE_ORDER)
    assert store.count() == 1


def test_buyer_history_matches_account_or_email(store):
    same_account = other_buyer(1, 3, payer_id=BASE_ORDER.payer_id)
    same_email = other_buyer(2, 2, email=BASE_ORDER.email)
    stranger = other_buyer(3, 1)
    for order in (same_account, same_email, stranger, BASE_ORDER):
        store.add(order)
    assert store.buyer_orders(BASE_ORDER) == [same_email, same_account]


def test_empty_ids_never_match_each_other(store):
    order = make_order(payer_id="", email="", address_key="")
    store.add(other_buyer(1, 1, payer_id="", email="", address_key=""))
    assert store.buyer_orders(order) == []
    assert store.address_orders(order) == []


def test_address_history_matches_the_address(store):
    same_address = other_buyer(1, 1, address_key=BASE_ORDER.address_key)
    store.add(same_address)
    store.add(other_buyer(2, 1))
    assert store.address_orders(BASE_ORDER) == [same_address]


def test_history_never_shows_later_orders(store):
    later = make_order(order_id="LATER123456", capture_id="LATERCAP", created_at=NOW + 60)
    store.add(later)
    assert store.buyer_orders(BASE_ORDER) == []
    assert store.address_orders(BASE_ORDER) == []
    assert store.shop_baseline(BASE_ORDER) == (0, 0.0)


def test_baseline_is_the_median_of_earlier_orders_in_this_currency(store):
    for number, cents in enumerate((1000, 2000, 9000), start=1):
        store.add(other_buyer(number, 1, amount_cents=cents))
    store.add(other_buyer(4, 1, amount_cents=100000, currency="SGD"))
    assert store.shop_baseline(BASE_ORDER) == (3, 2000)


def test_seeding_twice_adds_nothing_new(store):
    rows = build_rows()[:5]
    assert store.seed(rows, now=NOW) == 5
    assert store.seed(rows, now=NOW + 60) == 0
    assert store.count() == 5


def test_seed_rows_are_dated_and_marked_synthetic(store):
    row = build_rows()[0]
    store.seed([row], now=NOW)
    seeded = store.get(row["order_id"])
    assert seeded.source == "seed"
    assert seeded.created_at == NOW - row["days_ago"] * 86400


def test_shop_history_file_matches_its_generator():
    history = json.loads(config.SHOP_HISTORY.read_text(encoding="utf-8"))
    assert history == build_rows()


def test_shop_history_is_clearly_synthetic():
    rows = build_rows()
    assert all(row["email"].endswith("@example.com") for row in rows)
    assert all(row["order_id"].startswith("SEEDORDER") for row in rows)
