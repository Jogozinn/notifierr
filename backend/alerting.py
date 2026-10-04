from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ACTIONABLE_TIERS = ("GEM", "PROFITABLE", "REVIEW")
VERIFIED_PARTS_STATUSES = {"verified_screenshot", "verified_screenshot_and_page"}
ESTIMATED_PARTS_STATUSES = {"estimated", "verified_screenshot_low_confidence", "fallback", "manual_part_update"}
COMPONENT_FLAGS = {
    "accessory_not_phone",
    "battery_part_not_phone",
    "box_only",
    "camera_lens_part_not_phone",
    "charging_port_part_not_phone",
    "digitizer_not_phone",
    "display_assembly_not_phone",
    "glass_only_not_phone",
    "housing_not_phone",
    "lot_not_single_phone",
    "oled_lcd_part_not_phone",
    "part_only",
    "phone_not_included",
    "reader_adapter_not_phone",
    "replacement_part_not_phone",
    "replacement_screen",
    "screen_only",
    "screen_part_not_phone",
    "screen_protector_not_phone",
}


@dataclass(frozen=True)
class AlertDecision:
    eligible: bool
    tier: str | None
    blocking_reasons: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    surfaced_reasons: list[str] = field(default_factory=list)
    conservative_profit: float = 0.0
    expected_profit: float = 0.0
    upside_profit: float = 0.0
    expected_roi: float = 0.0
    confidence: str = "low"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_alert_decision(item: dict[str, Any], result: Any, current_settings: Any) -> AlertDecision:
    notification = getattr(current_settings, "notification_settings", {}) or {}
    current_profit_min = float(getattr(current_settings, "min_profit_to_alert", 75) or 75)
    gem_profit_min = float(notification.get("gem_min_expected_profit", current_profit_min) or current_profit_min)
    profitable_profit_min = float(notification.get("profitable_min_expected_profit", max(45, current_profit_min * 0.65)) or 0)
    review_profit_min = float(notification.get("review_min_expected_profit", 25) or 0)
    review_upside_min = float(notification.get("review_min_upside_profit", 60) or 0)
    gem_roi_min = _ratio(notification.get("gem_min_roi", 0.25))
    profitable_roi_min = _ratio(notification.get("profitable_min_roi", 0.15))
    review_roi_min = _ratio(notification.get("review_min_roi", 0.05))
    max_age_minutes = int(notification.get("max_listing_age_minutes", getattr(current_settings, "max_alert_item_age_minutes", 360)) or 360)

    conservative = float(item.get("profit_low") or getattr(result, "profit_low", 0) or 0)
    expected = float(item.get("profit_mid") or item.get("estimated_profit") or getattr(result, "profit_mid", 0) or 0)
    upside = float(item.get("profit_high") or getattr(result, "profit_high", 0) or 0)
    total_cost = float(item.get("total_cost") or 0)
    parts_cost = float(item.get("estimated_parts_cost") or getattr(result, "estimated_parts_cost", 0) or 0)
    investment = max(0.01, total_cost + parts_cost)
    expected_roi = expected / investment

    hard_flags = set(item.get("hard_reject_flags") or getattr(result, "hard_reject_flags", []) or [])
    classification_flags = set(item.get("listing_classification_flags") or getattr(result, "listing_classification_flags", []) or [])
    component_flags = sorted(flag for flag in hard_flags | classification_flags if flag in COMPONENT_FLAGS or flag.endswith("_not_phone"))
    blocking: list[str] = []
    if _is_unavailable(item):
        blocking.append("LISTING_UNAVAILABLE")
    if item.get("stale") or not item.get("fresh_for_active_queue", True):
        blocking.append("LISTING_STALE")
    if item.get("item_age_minutes") is not None and float(item["item_age_minutes"]) > max_age_minutes:
        blocking.append("LISTING_TOO_OLD")
    if item.get("user_status") in {"ignored", "rejected"}:
        blocking.append(f"USER_{str(item.get('user_status')).upper()}")
    score = float(item.get("score") or getattr(result, "score", 0) or 0)
    min_score = float(getattr(current_settings, "min_score_to_alert", 70) or 70)
    if score < min_score:
        blocking.append("SCORE_BELOW_USER_MINIMUM")
    if component_flags:
        blocking.append("COMPONENT_OR_ACCESSORY")
    for flag in sorted(hard_flags - set(component_flags)):
        blocking.append(f"HARD_RISK_{flag.upper()}")

    whole_phone_passed = bool(item.get("whole_phone_confidence_passed", getattr(result, "whole_phone_confidence_passed", False)))
    whole_phone_score = float(item.get("whole_phone_score") or getattr(result, "whole_phone_score", 0) or 0)
    model = str(item.get("model") or getattr(result, "model", "unknown") or "unknown")
    whole_phone_probable = whole_phone_passed or (whole_phone_score >= 3 and model != "unknown")
    has_repair_issue = bool(item.get("has_repair_issue", getattr(result, "has_repair_issue", False)))
    resale_available = float(item.get("resale_mid") or item.get("resale_value") or 0) > 0
    parts_available = bool(item.get("estimated_parts_cost_available", getattr(result, "estimated_parts_cost_available", False)))
    parts_status = str(item.get("parts_pricing_status") or getattr(result, "parts_pricing_status", "missing") or "missing")
    storage = item.get("storage_capacity") or getattr(result, "storage_capacity", None)
    carrier = _carrier_status(item, result)
    risk_flags = set(item.get("risk_flags") or getattr(result, "risk_flags", []) or [])
    manual_review_reason = str(item.get("manual_review_reason") or getattr(result, "manual_review_reason", "") or "").lower()
    evidence_risks = set()
    if "model_spec_mismatch" in classification_flags or "model/spec mismatch" in manual_review_reason:
        evidence_risks.add("MODEL_SPEC_MISMATCH")
    if "too cheap without proof" in manual_review_reason:
        evidence_risks.add("PRICE_IMPLAUSIBLE_WITHOUT_PROOF")
    if "parts-only listing lacks" in manual_review_reason:
        evidence_risks.add("WHOLE_PHONE_EVIDENCE_MISSING")
    auction = _is_auction(item)

    warnings: list[str] = []
    if not storage:
        warnings.append("STORAGE_UNKNOWN")
    if carrier in {"", "unknown", "none"}:
        warnings.append("CARRIER_UNKNOWN")
    if not str(item.get("raw_description") or "").strip():
        warnings.append("DESCRIPTION_MISSING")
    if parts_status in ESTIMATED_PARTS_STATUSES:
        warnings.append("PARTS_PRICE_ESTIMATED")
    if not parts_available:
        warnings.append("PARTS_PRICE_MISSING")
    if not resale_available:
        warnings.append("RESALE_PRICE_MISSING")
    if not has_repair_issue:
        warnings.append("REPAIR_SCOPE_UNCERTAIN")
    if auction:
        warnings.append("AUCTION_PRICE_NOT_FINAL")
    if risk_flags:
        warnings.extend(f"SOFT_RISK_{flag.upper()}" for flag in sorted(risk_flags))
    warnings.extend(sorted(evidence_risks))

    confidence = "high" if whole_phone_passed and model != "unknown" and resale_available and parts_status in VERIFIED_PARTS_STATUSES else (
        "medium" if whole_phone_probable and model != "unknown" and resale_available and parts_available else "low"
    )
    hard_blocked = bool(blocking)
    pricing_complete = resale_available and parts_available and has_repair_issue

    if (
        not hard_blocked
        and not auction
        and whole_phone_passed
        and pricing_complete
        and confidence == "high"
        and bool(storage)
        and carrier not in {"", "unknown", "none"}
        and score >= min_score
        and conservative >= gem_profit_min
        and expected_roi >= gem_roi_min
        and not risk_flags.intersection({"unknown_issue", "multiple_issues", "unknown_icloud", "untested"})
        and not evidence_risks
    ):
        return _decision("GEM", blocking, warnings, conservative, expected, upside, expected_roi, confidence, [
            "CONSERVATIVE_PROFIT_MEETS_GEM_MINIMUM",
            "STRONG_WHOLE_PHONE_AND_PRICING_CONFIDENCE",
            "REPAIR_SCOPE_UNDERSTOOD",
        ])

    if (
        not hard_blocked
        and not auction
        and whole_phone_passed
        and pricing_complete
        and confidence in {"medium", "high"}
        and expected >= profitable_profit_min
        and conservative >= -25
        and expected_roi >= profitable_roi_min
        and not evidence_risks
    ):
        return _decision("PROFITABLE", blocking, warnings, conservative, expected, upside, expected_roi, confidence, [
            "EXPECTED_PROFIT_MEETS_PROFITABLE_MINIMUM",
            "PLAUSIBLY_REPAIRABLE_WHOLE_PHONE",
        ])

    review_economics = expected >= review_profit_min or upside >= review_upside_min
    if (
        not hard_blocked
        and whole_phone_probable
        and model != "unknown"
        and resale_available
        and review_economics
        and (expected_roi >= review_roi_min or upside >= review_upside_min)
    ):
        return _decision("REVIEW", blocking, warnings, conservative, expected, upside, expected_roi, confidence, [
            "POSITIVE_EXPECTED_OR_UPSIDE_PROFIT",
            "MANUAL_VERIFICATION_CAN_RESOLVE_SOFT_UNCERTAINTY",
        ])

    if not whole_phone_probable:
        blocking.append("WHOLE_PHONE_NOT_PROBABLE")
    if model == "unknown":
        blocking.append("MODEL_UNKNOWN")
    if not resale_available:
        blocking.append("RESALE_UNAVAILABLE")
    if not review_economics:
        blocking.append("EXPECTED_PROFIT_BELOW_REVIEW_MINIMUM")
    elif expected_roi < review_roi_min and upside < review_upside_min:
        blocking.append("ROI_BELOW_REVIEW_MINIMUM")
    return AlertDecision(
        eligible=False,
        tier=None,
        blocking_reasons=_dedupe(blocking),
        soft_warnings=_dedupe(warnings),
        conservative_profit=round(conservative, 2),
        expected_profit=round(expected, 2),
        upside_profit=round(upside, 2),
        expected_roi=round(expected_roi, 4),
        confidence=confidence,
    )


def _decision(
    tier: str,
    blocking: list[str],
    warnings: list[str],
    conservative: float,
    expected: float,
    upside: float,
    expected_roi: float,
    confidence: str,
    surfaced: list[str],
) -> AlertDecision:
    return AlertDecision(
        eligible=True,
        tier=tier,
        blocking_reasons=_dedupe(blocking),
        soft_warnings=_dedupe(warnings),
        surfaced_reasons=surfaced,
        conservative_profit=round(conservative, 2),
        expected_profit=round(expected, 2),
        upside_profit=round(upside, 2),
        expected_roi=round(expected_roi, 4),
        confidence=confidence,
    )


def _ratio(value: Any) -> float:
    ratio = float(value or 0)
    return ratio / 100 if ratio > 1 else ratio


def _is_unavailable(item: dict[str, Any]) -> bool:
    return str(item.get("availability_status") or "").lower() in {"ended", "sold", "unavailable"}


def _is_auction(item: dict[str, Any]) -> bool:
    return str(item.get("buying_option_summary") or "").lower() == "auction"


def _carrier_status(item: dict[str, Any], result: Any) -> str:
    positive = set(item.get("positive_flags") or getattr(result, "positive_flags", []) or [])
    if "unlocked" in positive:
        return "unlocked"
    text = f"{item.get('title') or ''} {item.get('raw_description') or ''}".lower()
    if any(value in text for value in ("verizon", "at&t", " t-mobile", "tmobile", "sprint", "boost", "cricket", "metro")):
        return "carrier_known"
    return str(item.get("carrier_status") or "unknown")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
