"""Test data. CAPTURE and ORDER follow the shape of a real sandbox purchase, with made-up values."""
from __future__ import annotations

from dataclasses import replace

from app.orders import PaidOrder
from app.store import DAY

NOW = 1_800_000_000.0
CATEGORIES = ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"]
CAPTURE = {
    "id": "CAPTURE12345",
    "status": "COMPLETED",
    "amount": {"currency_code": "USD", "value": "149.00"},
    "seller_protection": {"status": "ELIGIBLE", "dispute_categories": CATEGORIES},
    "supplementary_data": {"related_ids": {"order_id": "ORDER12345A"}},
    "create_time": "2026-11-02T12:10:35Z",
}
ORDER = {
    "id": "ORDER12345A",
    "status": "COMPLETED",
    "create_time": "2026-11-02T12:10:01Z",
    "payment_source": {"paypal": {
        "email_address": "Buyer@Example.com",
        "account_id": "PAYER1234",
        "account_status": "VERIFIED",
        "name": {"given_name": "Alex", "surname": "Tan"},
        "address": {"country_code": "SG"},
    }},
    "purchase_units": [{
        "shipping": {
            "name": {"full_name": "Tan Alex"},
            "address": {"address_line_1": "1 Test Rd.", "admin_area_2": "Singapore",
                        "postal_code": "123456", "country_code": "SG"},
        },
        "payments": {"captures": [CAPTURE]},
    }],
    "payer": {
        "name": {"given_name": "Alex", "surname": "Tan"},
        "email_address": "Buyer@Example.com",
        "payer_id": "PAYER1234",
        "address": {"country_code": "SG"},
    },
}
BASE_ORDER = PaidOrder(
    order_id="ORDER12345A", capture_id="CAPTURE12345", payer_id="PAYER1234",
    email="buyer@example.com", payer_name="Alex Tan", ship_name="Tan Alex",
    ship_address="1 Test Rd., Singapore, 123456, SG", address_key="sg|123456|1testrd|",
    buyer_country="SG", ship_country="SG", amount_cents=14900, currency="USD",
    seller_protection="ELIGIBLE", protection_categories=tuple(CATEGORIES),
    account_status="VERIFIED", created_at=NOW,
)


def make_order(**changes) -> PaidOrder:
    return replace(BASE_ORDER, **changes)


def other_buyer(number: int, days_ago: float, **changes) -> PaidOrder:
    """An earlier order from someone else, to a different address."""
    return make_order(**{
        "order_id": f"OTHER{number:06d}", "capture_id": f"OTHERCAP{number:06d}",
        "payer_id": f"OTHERPAYER{number}", "email": f"other{number}@example.com",
        "address_key": f"sg|{number}|otherroad|", "created_at": NOW - days_ago * DAY, **changes})
