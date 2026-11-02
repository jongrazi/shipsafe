"""
Writes fixtures/shop_history.json: synthetic past orders, so a new install has a shop to
compare against. Every buyer is made up. The seed is fixed, so reruns give the same file.

    python scripts/make_shop_history.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "fixtures" / "shop_history.json"
SEED = 20261102
BUYERS = 140
ORDERS = 240
DISPUTED_ORDERS = 4
BOTH_CATEGORIES = ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"]
PRICES = ((3900, 25), (5900, 25), (8900, 20), (12900, 15), (14900, 15))  # cents, weight
COUNTRIES = (("US", 70), ("SG", 20), ("GB", 10))
PROTECTION = (("ELIGIBLE", 90), ("PARTIALLY_ELIGIBLE", 7), ("NOT_ELIGIBLE", 3))
ACCOUNT = (("VERIFIED", 85), ("UNVERIFIED", 15))


def pick(rng: random.Random, table: tuple) -> object:
    values, weights = zip(*table)
    return rng.choices(values, weights)[0]


def make_buyer(rng: random.Random, number: int) -> dict:
    country = pick(rng, COUNTRIES)
    name = f"Sample Buyer {number:03d}"
    street, postal = f"{number} Sample Street", f"{10000 + number}"
    return {
        "payer_id": f"SEEDBUYER{number:03d}",
        "email": f"sample-buyer-{number:03d}@example.com",
        "payer_name": name,
        "ship_name": name,
        "ship_address": f"{street}, {postal}, {country}",
        "address_key": "|".join((country.lower(), postal, street.replace(" ", "").lower(), "")),
        "buyer_country": country,
        "ship_country": country,
    }


def build_rows() -> list[dict]:
    rng = random.Random(SEED)
    buyers = [make_buyer(rng, number) for number in range(1, BUYERS + 1)]
    rows = []
    for number in range(1, ORDERS + 1):
        protection = pick(rng, PROTECTION)
        rows.append({
            **rng.choice(buyers),
            "order_id": f"SEEDORDER{number:04d}",
            "capture_id": f"SEEDCAPTURE{number:04d}",
            "amount_cents": pick(rng, PRICES),
            "currency": "USD",
            "seller_protection": protection,
            "protection_categories": [] if protection == "NOT_ELIGIBLE" else BOTH_CATEGORIES,
            "account_status": pick(rng, ACCOUNT),
            "days_ago": round(rng.uniform(2, 180), 3),  # none recent enough to affect velocity
            "disputed": False,
        })
    for row in rng.sample(rows, DISPUTED_ORDERS):
        row["disputed"] = True
    return sorted(rows, key=lambda row: row["days_ago"], reverse=True)


def main() -> None:
    OUTPUT.parent.mkdir(exist_ok=True)
    lines = ",\n".join(json.dumps(row) for row in build_rows())
    OUTPUT.write_text(f"[\n{lines}\n]\n", encoding="utf-8")
    print(f"Wrote {ORDERS} orders to {OUTPUT}")


if __name__ == "__main__":
    main()
