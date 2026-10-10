"""Read-only, point-in-time diagnostic for the existing Needs Data queue.

This module never rescored a listing or modifies its persisted status.  Stages
are explanatory *gates*, not labels that a listing has potential resale value.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


_COMPONENT_SUFFIX = "_not_phone"
_COMPONENT_FLAGS = {
    "lot_not_single_phone", "screen_part_not_phone", "replacement_part_not_phone",
    "phone_not_included", "part_only", "box_only", "replacement_screen",
    "screen_only", "housing_not_phone", "motherboard", "logic_board",
}


def diagnose_listing(item: dict[str, Any]) -> dict[str, Any]:
    """Explain why stored evidence cannot safely produce an actionable decision.

    A zero-valued profit is *not* evidence of unprofitability if estimated
    profit is unavailable.  Signal fields represent historical decisions;
    this function does not infer physical condition from the title.
    """
    flags = set(item.get("listing_classification_flags") or [])
    hard_flags = set(item.get("hard_reject_flags") or [])
    component_evidence = sorted(
        flag for flag in flags | hard_flags
        if flag.endswith(_COMPONENT_SUFFIX) or flag in _COMPONENT_FLAGS
    )
    item_type = str(item.get("item_type") or "ambiguous")
    model = str(item.get("model") or "unknown")
    model_known = model.lower() != "unknown"
    whole_phone_passed = item.get("whole_phone_confidence_passed") is True
    storage = item.get("storage_capacity") or None
    resale = float(item.get("resale_mid") or item.get("resale_value") or 0)
    resale_available = resale > 0
    has_repair = item.get("has_repair_issue") is True
    parts_available = item.get("estimated_parts_cost_available") is True
    profit_available = item.get("estimated_profit_available") is True
    profit = float(item.get("profit_mid") or item.get("estimated_profit") or 0) if profit_available else None
    note = str(item.get("manual_review_reason") or "")

    reasons: list[str] = []
    if component_evidence or item_type == "component":
        reasons.append("component_evidence")
    if item_type == "ambiguous" or not whole_phone_passed:
        reasons.append("whole_phone_not_verified")
    if not model_known:
        reasons.append("model_unknown")
    if not storage:
        reasons.append("storage_unknown")
    if not resale_available:
        reasons.append("resale_unavailable")
    if not has_repair:
        reasons.append("repair_scope_unknown")
    if not parts_available:
        reasons.append("repair_parts_unpriced")
    if not profit_available:
        reasons.append("profit_not_computable")
    if item.get("storage_resale_warning"):
        reasons.append("storage_resale_fallback")
    if "Low-confidence pricing" in note or "Only upside case works" in note:
        reasons.append("pricing_uncertain")
    if "Expected profit below threshold" in note:
        reasons.append("below_profit_threshold")

    # Stage ordering deliberately stops at an unresolved item type before
    # declaring missing resale to be an independent market-price problem.
    if component_evidence or item_type == "component":
        stage = "component_or_accessory_evidence"
        valuation_block = "component_gate" if not resale_available else None
    elif item_type != "whole_phone" or not whole_phone_passed:
        stage = "whole_phone_verification"
        valuation_block = (
            "whole_phone_gate" if not resale_available and item_type != "whole_phone"
            else "unconfirmed_resale_cause" if not resale_available
            else None
        )
    elif not model_known:
        stage = "model_identification"
        valuation_block = "model_gate" if not resale_available else None
    elif not resale_available:
        stage = "resale_lookup"
        valuation_block = "resale_lookup_or_baseline"
    elif not has_repair:
        stage = "repair_scope"
        valuation_block = None
    elif not parts_available:
        stage = "repair_parts_pricing"
        valuation_block = None
    elif not profit_available:
        stage = "profit_computation"
        valuation_block = None
    elif "Expected profit below threshold" in note:
        stage = "profit_qualification"
        valuation_block = None
    else:
        stage = "pricing_or_verification_review"
        valuation_block = None

    return {
        "item_id": str(item.get("item_id") or ""),
        "title": str(item.get("title") or ""),
        "primary_gate": stage,
        "observed_reasons": reasons,
        "resale_block_origin": valuation_block,
        "model": model,
        "storage_capacity": storage,
        "item_type": item_type,
        "item_type_reason": str(item.get("item_type_reason") or ""),
        "whole_phone_confidence_passed": whole_phone_passed,
        "whole_phone_score": item.get("whole_phone_score"),
        "has_repair_issue": has_repair,
        "estimated_parts_cost_available": parts_available,
        "estimated_profit_available": profit_available,
        "estimated_profit": profit,
        "resale_mid": resale if resale_available else None,
        "resale_source": str(item.get("resale_source") or ""),
        "resale_storage_warning": str(item.get("storage_resale_warning") or ""),
        "parts_pricing_status": str(item.get("parts_pricing_status") or ""),
        "manual_review_reason": note,
        "alert_tier": item.get("alert_tier"),
        "alert_blocking_reasons": list((item.get("alert_decision") or {}).get("blocking_reasons") or []),
        "classification_flags": sorted(flags),
        "hard_reject_flags": sorted(hard_flags),
        "positive_flags": list(item.get("positive_flags") or []),
        "description_available": (
            bool(item["has_raw_description"])
            if item.get("has_raw_description") is not None
            else bool(str(item.get("raw_description") or "").strip())
        ),
        "detail_fetch_status": str(item.get("detail_fetch_status") or "not_requested"),
        "detail_fetch_reason": str(item.get("detail_fetch_reason") or ""),
        "detail_fetch_attempted_at": item.get("detail_fetch_attempted_at"),
        "detail_fetch_recovered_fields": list(item.get("detail_fetch_recovered_fields") or []),
        "detail_fetch_failure_reason": str(item.get("detail_fetch_failure_reason") or ""),
        "detail_fetch_retry_after": item.get("detail_fetch_retry_after"),
        "pricing_version": {
            "scorer_hash": item.get("scorer_hash"),
            "rules_hash": item.get("rules_hash"),
            "resale_hash": item.get("resale_hash"),
        },
    }


def summarize_needs_data(
    items: list[dict[str, Any]], *, limit: int, source_rows: int, source_truncated: bool,
    hot_hours: int,
) -> dict[str, Any]:
    diagnosed = [diagnose_listing(item) for item in items]
    total = len(diagnosed)
    selected = diagnosed[:limit]
    gates = Counter(d["primary_gate"] for d in diagnosed)
    reasons = Counter(reason for d in diagnosed for reason in d["observed_reasons"])
    valuation = Counter(d["resale_block_origin"] for d in diagnosed if d["resale_block_origin"])
    fetch_statuses = Counter(d["detail_fetch_status"] for d in diagnosed)
    with_description = sum(1 for d in diagnosed if d["description_available"])
    return {
        "scope": "user_scoped_current_needs_data_queue",
        "read_only": True,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source_rows_examined": source_rows,
        "hot_hours": hot_hours,
        "source_truncated": source_truncated,
        "queue_total_within_source": total,
        "records_returned": len(selected),
        "complete": not source_truncated and len(selected) == total,
        "primary_gate_counts": dict(sorted(gates.items())),
        "observed_reason_counts": dict(sorted(reasons.items())),
        "resale_block_origins": dict(sorted(valuation.items())),
        "persisted_description_present": with_description,
        "detail_fetch_status_counts": dict(sorted(fetch_statuses.items())),
        "records": selected,
        "caveats": [
            "A stage is a diagnostic first gate, not evidence a phone is profitable or safe.",
            "Reason groups can overlap. Estimated profit is null when pricing cannot be computed.",
            "This is the current queue snapshot, not a reconstruction of historical decisions.",
            "Records are bounded to the dashboard source window and cap; check complete and source_truncated.",
        ],
    }


def decision_consistency_scan(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag potentially contradictory *current* UI decision snapshots.

    A pricing threshold in a human-readable note can refer to a stricter
    scoring policy than the notification tier; it is an investigation flag,
    not a claim that the decision is incorrect.
    """
    counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    flagged_items = 0
    for item in items:
        tier = str(item.get("alert_tier") or "")
        item_type = str(item.get("item_type") or "")
        note = str(item.get("manual_review_reason") or "")
        has_profit = item.get("estimated_profit_available") is True
        expected_profit = float(item.get("profit_mid") or item.get("estimated_profit") or 0) if has_profit else None
        flags: list[str] = []
        if tier in {"GEM", "PROFITABLE"}:
            if not has_profit:
                flags.append("actionable_tier_without_computable_profit")
            elif expected_profit is not None and expected_profit <= 0:
                flags.append("actionable_tier_with_nonpositive_profit")
            if item_type == "component":
                flags.append("component_with_actionable_tier")
            if "Expected profit below threshold" in note:
                flags.append("tier_vs_scoring_threshold_note")
        if not flags:
            continue
        counts.update(flags)
        flagged_items += 1
        if len(examples) < 25:
            examples.append({
                "item_id": str(item.get("item_id") or ""),
                "title": str(item.get("title") or ""),
                "tier": tier,
                "item_type": item_type,
                "estimated_profit": expected_profit,
                "manual_review_reason": note,
                "flags": flags,
            })
    return {
        "counts": dict(sorted(counts.items())),
        "examples": examples,
        "examples_capped": flagged_items > len(examples),
        "note": "Signals are current-snapshot inconsistencies to investigate, not proof of a sent erroneous notification.",
    }
