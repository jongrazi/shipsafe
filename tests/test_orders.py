"""Turning PayPal responses into PaidOrder records."""
from __future__ import annotations

import copy

import pytest

from app import orders
from tests.helpers import CAPTURE, ORDER


def test_reads_a_real_shaped_purchase():
    paid = orders.from_paypal(CAPTURE, ORDER)
    assert paid.order_id == "ORDER12345A"
    assert paid.capture_id == "CAPTURE12345"
    assert paid.payer_id == "PAYER1234"
    assert paid.email == "buyer@example.com"
    assert paid.payer_name == "Alex Tan"
    assert paid.ship_name == "Tan Alex"
    assert paid.ship_address == "1 Test Rd., Singapore, 123456, SG"
    assert (paid.buyer_country, paid.ship_country) == ("SG", "SG")
    assert (paid.amount_cents, paid.currency) == (14900, "USD")
    assert paid.seller_protection == "ELIGIBLE"
    assert paid.protection_categories == ("ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION")
    assert paid.account_status == "VERIFIED"
    assert paid.created_at == 1793621435.0


def test_uses_exact_cents():
    assert orders.from_paypal({**CAPTURE, "amount": {"value": "19.99"}}, ORDER).amount_cents == 1999


def test_missing_optional_fields_stay_empty():
    order = {"id": "ORDER12345A"}
    capture = {k: v for k, v in CAPTURE.items() if k != "seller_protection"}
    paid = orders.from_paypal(capture, order)
    assert paid.seller_protection == ""
    assert paid.protection_categories == ()
    assert (paid.payer_id, paid.email, paid.address_key) == ("", "", "")


@pytest.mark.parametrize("capture, order", [
    ({**CAPTURE, "status": "PENDING"}, ORDER),
    ({k: v for k, v in CAPTURE.items() if k != "amount"}, ORDER),
    ({**CAPTURE, "amount": {"value": "not a number"}}, ORDER),
    ({**CAPTURE, "amount": {"value": "NaN"}}, ORDER),
    (CAPTURE, {}),
])
def test_refuses_anything_but_a_completed_payment(capture, order):
    assert orders.from_paypal(capture, order) is None


def test_address_key_ignores_case_spacing_and_punctuation():
    first = {"address_line_1": "1 Test Rd.", "postal_code": "123456", "country_code": "SG"}
    second = {"address_line_1": "1  TEST RD", "postal_code": "123456", "country_code": "sg"}
    assert orders.address_key(first) == orders.address_key(second)


def test_address_key_is_empty_without_a_street():
    assert orders.address_key({"country_code": "SG"}) == ""


def test_finds_order_id_in_supplementary_data():
    assert orders.order_id_of_capture(CAPTURE) == "ORDER12345A"


def test_finds_order_id_in_the_up_link():
    capture = copy.deepcopy(CAPTURE)
    del capture["supplementary_data"]
    capture["links"] = [
        {"rel": "self", "href": "https://api.sandbox.paypal.com/v2/payments/captures/CAPTURE12345"},
        {"rel": "up", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORDER12345A"},
    ]
    assert orders.order_id_of_capture(capture) == "ORDER12345A"


@pytest.mark.parametrize("links", [
    [],
    [{"rel": "up", "href": "https://api.sandbox.paypal.com/v2/payments/authorizations/AUTH123456"}],
    [{"rel": "up", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/../../evil"}],
])
def test_gives_no_order_id_without_a_valid_link(links):
    assert orders.order_id_of_capture({"links": links}) is None
