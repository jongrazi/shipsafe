"""
Risk signals: plain code, no AI.

Each signal is a fact about an order with a level: ok, info, watch or risk. The AI review
reads these and investigates further, and it can't change them.

Summaries are built only from numbers and PayPal's fixed status values, never from names,
emails or addresses. Buyers write those, so they can't be used to steer the AI from here.

The thresholds are starting points, to be tuned on the tuning orders only.
"""
from __future__ import annotations

from dataclasses import dataclass

from .orders import PaidOrder
from .store import DAY, Store

MIN_BASELINE_ORDERS = 20    # fewer than this and a shop's "normal" isn't known
VALUE_WATCH_RATIO = 2.0     # times the shop's median order
VALUE_RISK_RATIO = 4.0
BUYER_WINDOW = 1 * DAY
BUYER_WATCH_ORDERS = 1      # earlier orders from this buyer inside the window
BUYER_RISK_ORDERS = 2
ADDRESS_WINDOW = 30 * DAY
ADDRESS_WATCH_BUYERS = 1    # other buyers at this address inside the window
ADDRESS_RISK_BUYERS = 3


@dataclass(frozen=True)
class Signal:
    name: str
    level: str      # ok | info | watch | risk
    summary: str


def build(order: PaidOrder, store: Store) -> list[Signal]:
    buyer_history = store.buyer_orders(order)
    address_history = store.address_orders(order)
    return [
        seller_protection(order),
        _account_status(order),
        _country(order),
        _name(order),
        _buyer_history(buyer_history),
        _buyer_velocity(order, buyer_history),
        _address_sharing(order, address_history),
        _past_disputes(buyer_history, address_history),
        _order_value(order, store),
    ]


PROTECTION_CATEGORIES = {
    "ITEM_NOT_RECEIVED": "item-not-received claims",
    "UNAUTHORIZED_TRANSACTION": "unauthorized payments",
}


def seller_protection(order: PaidOrder) -> Signal:
    status = order.seller_protection
    covers = _plain_list([PROTECTION_CATEGORIES.get(category, category.replace("_", " ").lower())
                          for category in order.protection_categories]) or "no listed categories"
    if status == "ELIGIBLE":
        return Signal("seller_protection", "ok", f"Eligible for Seller Protection, covering {covers}.")
    if status == "PARTIALLY_ELIGIBLE":
        return Signal("seller_protection", "watch", f"Only partly eligible, covering {covers}.")
    if status == "NOT_ELIGIBLE":
        return Signal("seller_protection", "risk", "Not eligible for Seller Protection.")
    return Signal("seller_protection", "watch", "PayPal gave no Seller Protection status.")


def _plain_list(items: list[str]) -> str:
    """"a", "a and b", "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + (f" and {items[-1]}" if items else "")


def _account_status(order: PaidOrder) -> Signal:
    if order.account_status == "VERIFIED":
        return Signal("account_status", "ok", "The buyer's PayPal account is verified.")
    if order.account_status == "UNVERIFIED":
        return Signal("account_status", "watch", "The buyer's PayPal account is unverified.")
    return Signal("account_status", "info", "PayPal gave no account status.")


def _country(order: PaidOrder) -> Signal:
    if not (order.buyer_country and order.ship_country):
        return Signal("country", "info", "The buyer or shipping country is unknown.")
    if order.buyer_country != order.ship_country:
        return Signal("country", "watch",
                      f"Shipping to {order.ship_country}, but the buyer's account is in "
                      f"{order.buyer_country}.")
    return Signal("country", "ok", "The buyer's country matches the shipping country.")


def _name(order: PaidOrder) -> Signal:
    payer, recipient = _name_words(order.payer_name), _name_words(order.ship_name)
    if not (payer and recipient):
        return Signal("name", "info", "The buyer or recipient name is missing.")
    if payer <= recipient or recipient <= payer:  # PayPal can return names in either order
        return Signal("name", "ok", "The buyer's name matches the recipient's.")
    return Signal("name", "watch", "The recipient's name differs from the buyer's.")


def _name_words(name: str) -> set[str]:
    return set(name.casefold().split())


def _buyer_history(history: list[PaidOrder]) -> Signal:
    if not history:
        return Signal("buyer_history", "info", "First order from this buyer.")
    return Signal("buyer_history", "ok", f"{len(history)} earlier order(s) from this buyer.")


def _buyer_velocity(order: PaidOrder, history: list[PaidOrder]) -> Signal:
    recent = sum(o.created_at >= order.created_at - BUYER_WINDOW for o in history)
    level = ("risk" if recent >= BUYER_RISK_ORDERS else
             "watch" if recent >= BUYER_WATCH_ORDERS else "ok")
    return Signal("buyer_velocity", level, f"{recent} other order(s) from this buyer in 24 hours.")


def _address_sharing(order: PaidOrder, history: list[PaidOrder]) -> Signal:
    others = {o.payer_id or o.email for o in history
              if o.created_at >= order.created_at - ADDRESS_WINDOW
              and o.payer_id != order.payer_id and o.email != order.email}
    level = ("risk" if len(others) >= ADDRESS_RISK_BUYERS else
             "watch" if len(others) >= ADDRESS_WATCH_BUYERS else "ok")
    return Signal("address_sharing", level, f"{len(others)} other buyer(s) at this address in 30 days.")


def _past_disputes(buyer_history: list[PaidOrder], address_history: list[PaidOrder]) -> Signal:
    disputed = {o.order_id for o in buyer_history + address_history if o.disputed}
    if disputed:
        return Signal("past_disputes", "risk",
                      f"{len(disputed)} earlier order(s) from this buyer or address were disputed.")
    return Signal("past_disputes", "ok", "No disputes on earlier orders from this buyer or address.")


def _order_value(order: PaidOrder, store: Store) -> Signal:
    count, median = store.shop_baseline(order)
    if count < MIN_BASELINE_ORDERS or median <= 0:
        return Signal("order_value", "info", f"Only {count} earlier orders, too few to judge value.")
    ratio = order.amount_cents / median
    level = ("risk" if ratio >= VALUE_RISK_RATIO else
             "watch" if ratio >= VALUE_WATCH_RATIO else "ok")
    return Signal("order_value", level, f"{ratio:.1f} times the shop's median order.")
