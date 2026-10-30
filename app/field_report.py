"""
The first-day test: which fields does a real sandbox order actually return?

The review design relies on these fields, so this reports them for each order. Status
fields show their value. Personal fields show only present or missing, so buyer details
stay out of the logs. Full responses go to data/samples, which git ignores.
"""
from __future__ import annotations

import json
from pathlib import Path

CAPTURE_VALUES = ("status", "seller_protection.status", "seller_protection.dispute_categories",
                  "processor_response.avs_code", "processor_response.cvv_code")
ORDER_VALUES = ("status", "payment_source.paypal.account_status",
                "payment_source.paypal.address.country_code",
                "purchase_units.0.shipping.address.country_code",
                "purchase_units.0.shipping.type", "purchase_units.0.items.0.category")
ORDER_PERSONAL = ("payment_source.paypal.email_address", "payment_source.paypal.account_id",
                  "payer.payer_id", "purchase_units.0.shipping.name.full_name",
                  "purchase_units.0.shipping.address.address_line_1",
                  "purchase_units.0.shipping.address.postal_code")


def dig(data, path: str):
    """Follow a dotted path through dicts and lists. Missing steps give None."""
    for step in path.split("."):
        if isinstance(data, dict):
            data = data.get(step)
        elif isinstance(data, list) and step.isdigit() and int(step) < len(data):
            data = data[int(step)]
        else:
            return None
    return data


def first_capture(order: dict) -> dict:
    return dig(order, "purchase_units.0.payments.captures.0") or {}


def build(capture: dict, order: dict) -> dict:
    report: dict = {}
    for path in CAPTURE_VALUES:
        value = dig(capture, path)
        report[f"capture.{path}"] = "missing" if value is None else value
    for path in ORDER_VALUES:
        value = dig(order, path)
        report[f"order.{path}"] = "missing" if value is None else value
    for path in ORDER_PERSONAL:
        report[f"order.{path}"] = "missing" if dig(order, path) is None else "present"
    return report


def save_sample(directory: Path, name: str, capture: dict, order: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps({"capture": capture, "order": order}, indent=2), encoding="utf-8")
