"""Small user-reported business outcome model, separate from estimated profit."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


OUTCOME_STATUSES = frozenset({"SKIPPED", "PURCHASED", "SOLD", "FAILED_REPAIR"})
MONEY_FIELDS = (
    "purchase_price", "purchase_tax", "inbound_shipping", "parts_cost",
    "other_repair_cost", "sale_price", "selling_fees", "outbound_shipping",
    "refund_amount", "other_cost",
)


def actual_net_profit(status: str, values: dict[str, Any]) -> float | None:
    """Return net realized proceeds only for a sale with price and acquisition cost."""
    if status not in OUTCOME_STATUSES:
        raise ValueError("Unsupported outcome status")
    for field in MONEY_FIELDS:
        value = values.get(field)
        if value is not None and Decimal(str(value)) < 0:
            raise ValueError(f"{field} must be nonnegative")
    if status != "SOLD" or values.get("sale_price") is None or values.get("purchase_price") is None:
        return None
    proceeds = Decimal(str(values["sale_price"]))
    costs = sum(
        (Decimal(str(values.get(field) or 0)) for field in MONEY_FIELDS if field != "sale_price"),
        Decimal("0"),
    )
    return float((proceeds - costs).quantize(Decimal("0.01")))
