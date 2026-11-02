"""
Turns PayPal's capture and order responses into one flat record.

PayPal's response shapes stay in this file. The store, the signals and the review all
work with PaidOrder, so a change in PayPal's JSON has one place to land.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .field_report import dig

PAYPAL_ID = re.compile(r"^[A-Z0-9]{10,40}$")


@dataclass(frozen=True)
class PaidOrder:
    order_id: str
    capture_id: str
    payer_id: str
    email: str
    payer_name: str
    ship_name: str
    ship_address: str            # for showing to the seller
    address_key: str             # for matching: see address_key() below
    buyer_country: str
    ship_country: str
    amount_cents: int
    currency: str
    seller_protection: str       # ELIGIBLE | PARTIALLY_ELIGIBLE | NOT_ELIGIBLE, or "" if absent
    protection_categories: tuple[str, ...]
    account_status: str          # VERIFIED | UNVERIFIED, or "" if absent
    created_at: float            # epoch seconds
    disputed: bool = False
    source: str = "paypal"       # paypal | seed (synthetic shop history)


def address_key(address: dict) -> str:
    """Same address, whatever the case, spacing or punctuation. "" if there is no street."""
    if not (address.get("address_line_1") or address.get("postal_code")):
        return ""
    parts = (address.get(field) or "" for field in
             ("country_code", "postal_code", "address_line_1", "address_line_2"))
    return "|".join(re.sub(r"\W", "", part.casefold()) for part in parts)


def order_id_of_capture(capture: dict) -> str | None:
    """A capture points back to its order through supplementary_data or its "up" link."""
    order_id = dig(capture, "supplementary_data.related_ids.order_id")
    if not order_id:
        for link in capture.get("links") or []:
            href = str(link.get("href", ""))
            if link.get("rel") == "up" and "/checkout/orders/" in href:
                order_id = href.rstrip("/").rsplit("/", 1)[-1]
    return order_id if isinstance(order_id, str) and PAYPAL_ID.match(order_id) else None


def from_paypal(capture: dict, order: dict) -> PaidOrder | None:
    """Return the order, or None unless it is a completed payment with an amount."""
    amount = _cents(dig(capture, "amount.value"))
    if not (order.get("id") and capture.get("id")) or amount is None:
        return None
    if capture.get("status") != "COMPLETED":
        return None

    wallet = dig(order, "payment_source.paypal") or {}
    payer = order.get("payer") or {}
    shipping = dig(order, "purchase_units.0.shipping") or {}
    address = shipping.get("address") or {}
    protection = capture.get("seller_protection") or {}
    return PaidOrder(
        order_id=order["id"],
        capture_id=capture["id"],
        payer_id=payer.get("payer_id") or wallet.get("account_id") or "",
        email=(payer.get("email_address") or wallet.get("email_address") or "").casefold(),
        payer_name=_full_name(wallet.get("name") or payer.get("name") or {}),
        ship_name=_full_name(shipping.get("name") or {}),
        ship_address=", ".join(filter(None, (
            address.get("address_line_1"), address.get("address_line_2"),
            address.get("admin_area_2"), address.get("postal_code"),
            address.get("country_code")))),
        address_key=address_key(address),
        buyer_country=dig(payer, "address.country_code") or dig(wallet, "address.country_code") or "",
        ship_country=address.get("country_code") or "",
        amount_cents=amount,
        currency=dig(capture, "amount.currency_code") or "",
        seller_protection=protection.get("status") or "",
        protection_categories=tuple(protection.get("dispute_categories") or ()),
        account_status=wallet.get("account_status") or "",
        created_at=_epoch(capture.get("create_time") or order.get("create_time")),
    )


def _cents(value) -> int | None:
    try:
        return int(Decimal(str(value)) * 100)
    except (InvalidOperation, ValueError):  # ValueError also covers NaN and Infinity
        return None


def _full_name(name: dict) -> str:
    return name.get("full_name") or " ".join(
        filter(None, (name.get("given_name"), name.get("surname"))))


def _epoch(timestamp: str | None) -> float:
    try:
        return datetime.fromisoformat((timestamp or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()
