"""
Prints the latest reviews from the local database and the last 24 hours' estimated cost,
until the seller screen exists. Costs are estimates from token counts, not a bill.

    python scripts/show_reviews.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.review import estimated_cost  # noqa: E402
from app.store import DAY, Store  # noqa: E402


def cost_text(review: dict) -> str:
    cost = estimated_cost(review["model"], review["usage"])
    return "unknown" if cost is None else f"${cost:.4f}"


def main() -> None:
    store = Store(config.DB_PATH)
    reviews = store.latest_reviews()
    if not reviews:
        print("No reviews yet.")
    for item in reviews:
        decision, review = item["record"]["decision"], item["record"]["review"]
        print(f"\nOrder {item['order_id']}: {item['action'].upper()} (band {item['band']})")
        print(f"  Rules: {decision['rule_band']}. Model: {decision['model_band']}.")
        print(f"  Why: {decision['reason']}")
        print(f"  Explanation: {review['explanation'] or '(none)'}")
        for reason in review["reasons"]:
            print(f"    - {reason}")
        if review["buyer_message"]:
            print(f"  Draft to buyer: {review['buyer_message']}")
        if review["errors"]:
            print(f"  Errors: {review['errors']}")
        tokens = [(turn["input_tokens"], turn["output_tokens"]) for turn in review["usage"]]
        print(f"  {review['model']}, tokens by turn (in, out): {tokens}, estimated {cost_text(review)}")

    today = store.reviews_since(time.time() - DAY)
    called = [item["record"]["review"] for item in today if item["record"]["review"]["usage"]]
    costs = [estimated_cost(review["model"], review["usage"]) for review in called]
    total = sum(cost for cost in costs if cost is not None)
    print(f"\nLast 24 hours: {len(today)} reviews, {len(called)} called Claude, "
          f"estimated ${total:.2f}. Daily limit: {config.DAILY_REVIEW_LIMIT}.")


if __name__ == "__main__":
    main()
