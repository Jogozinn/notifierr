from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


HARD_REJECT_PATTERNS = {
    "water_damage": r"\b(water|liquid)\s+damage(?:d)?\b",
    "icloud_locked": r"\bicloud\s+lock(?:ed)?\b|\bapple\s+id\s+lock(?:ed)?\b",
    "activation_locked": r"\bactivation\s+lock(?:ed)?\b|\blocked\s+to\s+owner\b|\bowner\s+locked\b",
    "mdm_locked": r"\bmdm\s+lock(?:ed)?\b|\bremote\s+management\b",
    "blacklisted": r"\bblack\s*list(?:ed)?\b",
    "bad_esn": r"\bbad\s+esn\b",
    "financed": r"\bfinanc(?:ed|ing)\b|\bpayments?\s+owed\b",
    "no_service": r"\bno\s+service\b",
    "baseband": r"\bbaseband\b",
    "logic_board": r"\blogic\s+board\b",
    "motherboard": r"\bmother\s*board\b",
    "bent_frame": r"\bbent\s+frame\b",
    "major_frame_damage": r"\bmajor\s+frame\s+damage\b|\bframe\s+(?:is\s+)?(?:badly\s+)?damaged\b",
    "no_power": r"\bno\s+power\b|\bdoes\s+not\s+power\s+on\b|\bdoesn'?t\s+power\s+on\b|\bwon'?t\s+power\s+on\b|\bnot\s+powering\s+on\b",
    "does_not_turn_on": r"\bdoes\s+not\s+turn\s+on\b|\bdoesn'?t\s+turn\s+on\b|\bwon'?t\s+turn\s+on\b",
    "face_id_not_working": r"\bface\s*id\s+(?:not\s+working|does\s+not\s+work|doesn'?t\s+work|broken|fail(?:ed|s)?)\b",
}

NOT_PHONE_PATTERNS = {
    "reader_adapter_not_phone": r"\b(?:sd\s+card\s+reader|card\s+reader|usb\s*c\s+reader|adapter|cable)\b",
    "accessory_not_phone": r"\b(?:gimbal|stabilizer|hi[-\s]?fi\s+system|speaker\s+system|ipod\s+dock)\b",
    "screen_protector_not_phone": r"\b(?:screen\s+protector|tempered\s+glass|privacy\s+glass)\b",
    "screen_part_not_phone": r"\b(?:screen\s+assembly\s+parts\s+only|cracked\s+glass\s+iphone\s+\d{2,}.*\bscreen\b)\b",
    "display_assembly_not_phone": r"\b(?:display\s+screen\s+digitizer|display\s+digitizer|screen\s+digitizer\s+assembly|digitizer\s+assembly)\b",
    "digitizer_not_phone": r"\bdigitizer\b",
    "oled_lcd_part_not_phone": r"\b(?:oled\s+lcd\s+original|oem\s+oled\s+lcd\s+screen|lcd\s+screen\s+glass)\b",
    "glass_only_not_phone": r"\bglass\s+only\b",
    "lot_not_single_phone": r"\b(?:lot\s+of\s+\d+|\d+x\s+oem\s+original|parts\s+only\s+lot|lot\s+of\s+phones|phone\s+lot|mixed\s+models|apple\s+iphone\s+lot|set\s+of\s+\d+|parts\s+harvesting\s+only)\b",
    "camera_lens_part_not_phone": r"\b(?:camera\s+protector|camera\s+ring|camera\s+lens\s+protector|front\s+camera\s+for\s+iphone|rear\s+camera\s+for\s+iphone)\b",
    "housing_not_phone": r"\b(?:rear\s+housing|back\s+housing|\bhousing\b|chassis|housing\s+with\s+small\s+parts|back\s+glass\s+rear\s+housing)\b",
    "battery_part_not_phone": r"\bbattery\s+for\s+iphone\b",
    "charging_port_part_not_phone": r"\bcharging\s+port\s+(?:flex\s+)?(?:cable\s+)?for\s+iphone\b|\bcharging\s+port\s+flex\b",
    "replacement_part_not_phone": r"\b(?:replacement\s+part|repair\s+part|flex\s+cable)\b",
    "logic_board": r"\blogic\s+board\b",
    "motherboard": r"\bmother\s*board\b",
}

POSITIVE_PATTERNS = {
    "cracked_screen": r"\bcracked\s+screen\b|\bscreen\s+(?:is\s+)?cracked\b",
    "screen_display_issue": r"\b(?:screen\s+lines|vertical\s+lines|black\s+spot|bad\s+lcd|damaged\s+lcd|lcd\s+screen(?:\s+damaged)?|screen\s+has\s+lines|display\s+lines)\b",
    "bad_battery": r"\bbad\s+battery\b|\bswollen\s+battery\b|\bbattery\s+(?:service|needs\s+replacement|issue)\b",
    "back_glass_cracked": r"\bback\s+glass\s+cracked\b|\bcracked\s+back\s+glass\b|\bcracked\s+back\b",
    "camera_lens_cracked": r"\bcamera\s+lens\s+cracked\b|\bcracked\s+camera\s+lens\b",
    "charging_port_issue": r"\bcharging\s+port\s+(?:issue|problem|bad|broken)\b|\bdoes\s+not\s+charge\b",
    "bad_oled": r"\bbad\s+oled\b",
    "powers_on": r"\bpowers?\s+on\b|\bturns?\s+on\b",
    "unlocked": r"\bfactory\s+unlocked\b|\bcarrier\s+unlocked\b|\bunlocked\b",
    "clean_imei": r"\bclean\s+imei\b|\bclean\s+esn\b",
    "face_id_works": r"\bface\s*id\s+works\b|\bface\s*id\s+working\b",
}

PROOF_PATTERNS = {
    "proof_tested": r"\btested\b",
    "proof_icloud_off": r"\bicloud\s+(?:off|removed|signed\s+out)\b",
    "proof_no_icloud_lock": r"\bno\s+icloud\s+lock(?:ed)?\b|\bnot\s+icloud\s+lock(?:ed)?\b",
    "proof_touch_works": r"\btouch\s+(?:works|working|is\s+working)\b",
    "proof_display_works": r"\bdisplay\s+(?:works|working|is\s+working)\b|\blcd\s+(?:works|working|is\s+working)\b|\boled\s+(?:works|working|is\s+working)\b",
    "proof_fully_functional_except_issue": r"\bfully\s+functional\s+except\b|\beverything\s+works\s+except\b|\bonly\s+issue\s+is\b",
}

RISK_PATTERNS = {
    "read_description": r"\bread\s+description\b",
    "for_parts": r"\bfor\s+parts\b|\bparts\s+only\b",
    "as_is": r"\bas[-\s]?is\b",
    "untested": r"\buntested\b",
    "unknown_issue": r"\bunknown\s+(?:issue|problem|condition)\b",
    "multiple_issues": r"\bmultiple\s+issues\b",
    "ic_issue": r"\b(?:no\s+ic|bad\s+ic|ic\s+(?:issue|problem|bad))\b",
}

ISSUE_COST_KEYS = {
    "cracked_screen": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
    "screen_display_issue": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
    "bad_oled": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
    "bad_battery": ("parts.battery", "battery_cost"),
    "back_glass_cracked": ("parts.back_glass", "back_glass_cost"),
    "camera_lens_cracked": ("parts.camera_lens", "camera_lens_cost"),
    "charging_port_issue": ("parts.charging_port", "charging_port_cost"),
}

VERIFIED_PART_STATUSES = {"verified_screenshot", "verified_screenshot_and_page"}
UNVERIFIED_PART_STATUSES = {"estimated", "verified_screenshot_low_confidence", "fallback"}
STRICT_MARGIN_PART_STATUSES = {"estimated", "verified_screenshot_low_confidence"}

VERIFIED_PARTS_LABEL = "Verified parts"
PARTS_ESTIMATE_NOT_VERIFIED_LABEL = "Parts estimate not verified"
PART_PRICE_MISSING_LABEL = "Actual part price not on file"
RESALE_MISSING_LABEL = "Resale value missing"
MODEL_UNKNOWN_LABEL = "Model unknown"
NO_REPAIR_ISSUE_LABEL = "No detected repair issue"

PART_PRICE_MISSING_WARNING = "Estimated profit unavailable — part price missing"
PART_PRICE_OR_ISSUE_MISSING_WARNING = "Estimated profit unavailable — part price missing or issue unknown"
RESALE_MISSING_WARNING = "Estimated profit unavailable — resale value missing"
MODEL_UNKNOWN_WARNING = "Model unknown — manual review needed"
NO_REPAIR_ISSUE_WARNING = "Estimated profit unavailable — no specific repair issue detected"

ACTUAL_REPAIR_ISSUE_FLAGS = {
    "cracked_screen",
    "screen_display_issue",
    "bad_oled",
    "bad_battery",
    "back_glass_cracked",
    "camera_lens_cracked",
    "charging_port_issue",
}

OLD_MODEL_IGNORED_FLAG = "old_model_ignored"

BLOCKING_RISK_FLAGS = {"as_is", "untested", "unknown_issue", "multiple_issues", "ic_issue"}

GENERIC_REPAIR_ISSUE_PATTERN = re.compile(
    r"\b(?:broken|for\s+repair|parts\s+only|for\s+parts|read\s+description|parts\s*/\s*repair)\b",
    re.IGNORECASE,
)

STORAGE_PATTERN = re.compile(r"\b(?:64|128|256|512)\s*gb\b|\b1\s*tb\b", re.IGNORECASE)
IPHONE_TITLE_PATTERN = re.compile(r"\b(?:apple\s+)?iphone(?:\s+\d{2,}|(?:\s+(?:se|xr|xs|x|pro|max|plus|mini)){0,4})\b", re.IGNORECASE)
CARRIER_PATTERN = re.compile(r"\b(?:unlocked|factory\s+unlocked|carrier\s+unlocked|verizon|at&t|att|tmobile|t-mobile|sprint|boost|cricket|metro)\b", re.IGNORECASE)
WHOLE_PHONE_CATEGORY_PATTERN = re.compile(r"cell\s+phones?\s*&\s*smartphones?", re.IGNORECASE)
WHOLE_PHONE_CONDITION_PATTERN = re.compile(r"\b(?:used|for\s+parts|not\s+working)\b", re.IGNORECASE)
PARTS_ONLY_PATTERN = re.compile(r"\bparts\s+only\b", re.IGNORECASE)
DISPLAY_PART_PATTERN = re.compile(
    r"\b(?:screen|display|oled|lcd|digitizer|screen\s+glass|screen\s+assembly|glass\s+assembly)\b",
    re.IGNORECASE,
)
SCREEN_PART_LISTING_PATTERN = re.compile(
    r"\b(?:oem\s+(?:screen|display)|(?:oled|lcd)\s+(?:screen|display)|screen\s+glass\s+oled(?:\s+lcd)?|display\s+(?:screen\s+)?(?:digitizer|replacement)|display\s+screen\s+replacement|screen\s+digitizer|good\s+(?:lcd|oled)(?:\s*&\s*touch)?|only\s+display|for\s+lcd\s+parts|screen\s+assembly|original\s+.*oled\s+screen)\b",
    re.IGNORECASE,
)
FULL_DEVICE_PATTERN = re.compile(r"\b(?:phone|device|works|powers?\s+on|turns?\s+on|clean\s+imei|face\s*id)\b", re.IGNORECASE)
OLD_MODEL_PATTERN = re.compile(r"\biphone\s*(?:3g|3gs|4s?|5c|5s|5|6s?|7|8)(?:\s+plus)?\b", re.IGNORECASE)
IPHONE_13_64GB_PATTERN = re.compile(r"\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b.*\b64\s*gb\b|\b64\s*gb\b.*\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b", re.IGNORECASE)
IPHONE_13_5_5IN_PATTERN = re.compile(r"\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b.*\b5\.5\s*(?:in|inch|inches)\b|\b5\.5\s*(?:in|inch|inches)\b.*\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b", re.IGNORECASE)
KNOWN_MODEL_ALIASES = (
    "iPhone 17 Pro Max",
    "iPhone 17 Pro",
    "iPhone 17",
    "iPhone 16 Pro Max",
    "iPhone 16 Pro",
    "iPhone 16",
    "iPhone 15 Pro Max",
    "iPhone 15 Pro",
    "iPhone 15",
    "iPhone 14 Pro Max",
    "iPhone 14 Pro",
    "iPhone 14 Plus",
    "iPhone 14",
    "iPhone 13 Pro Max",
    "iPhone 13 Pro",
    "iPhone 13 Mini",
    "iPhone 13",
    "iPhone 12 Pro Max",
    "iPhone 12 Pro",
    "iPhone 12 Mini",
    "iPhone 12",
    "iPhone 11 Pro Max",
    "iPhone 11 Pro",
    "iPhone 11",
    "iPhone XS Max",
    "iPhone XS",
    "iPhone XR",
    "iPhone X",
    "iPhone SE 3rd Gen",
    "iPhone SE 2nd Gen",
    "iPhone SE",
)


@dataclass
class ScoreResult:
    score: float
    status: str
    model: str
    estimated_profit: float
    resale_value: float
    estimated_parts_cost: float
    risk_buffer: float
    resale_low: float = 0.0
    resale_mid: float = 0.0
    resale_high: float = 0.0
    profit_low: float = 0.0
    profit_mid: float = 0.0
    profit_high: float = 0.0
    resale_confidence: str = ""
    resale_sample_size: int = 0
    resale_note: str = ""
    parts_pricing_status: str = "fallback"
    parts_pricing_note: str = ""
    parts_pricing_label: str = PARTS_ESTIMATE_NOT_VERIFIED_LABEL
    pricing_warning: str = PARTS_ESTIMATE_NOT_VERIFIED_LABEL
    estimated_profit_available: bool = True
    estimated_parts_cost_available: bool = True
    manual_review_allowed: bool = False
    whole_phone_confidence_passed: bool = False
    whole_phone_score: float = 0.0
    has_repair_issue: bool = False
    manual_review_needed: bool = False
    manual_review_reason: str = ""
    alert_eligible: bool = False
    listing_classification_flags: list[str] = field(default_factory=list)
    hard_reject_flags: list[str] = field(default_factory=list)
    positive_flags: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)

    def as_item_fields(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status,
            "model": self.model,
            "estimated_profit": self.estimated_profit,
            "resale_value": self.resale_value,
            "resale_low": self.resale_low,
            "resale_mid": self.resale_mid,
            "resale_high": self.resale_high,
            "profit_low": self.profit_low,
            "profit_mid": self.profit_mid,
            "profit_high": self.profit_high,
            "resale_confidence": self.resale_confidence,
            "resale_sample_size": self.resale_sample_size,
            "resale_note": self.resale_note,
            "estimated_parts_cost": self.estimated_parts_cost,
            "risk_buffer": self.risk_buffer,
            "parts_pricing_status": self.parts_pricing_status,
            "parts_pricing_note": self.parts_pricing_note,
            "parts_pricing_label": self.parts_pricing_label,
            "pricing_warning": self.pricing_warning,
            "estimated_profit_available": self.estimated_profit_available,
            "estimated_parts_cost_available": self.estimated_parts_cost_available,
            "manual_review_allowed": self.manual_review_allowed,
            "whole_phone_confidence_passed": self.whole_phone_confidence_passed,
            "whole_phone_score": self.whole_phone_score,
            "has_repair_issue": self.has_repair_issue,
            "manual_review_needed": self.manual_review_needed,
            "manual_review_reason": self.manual_review_reason,
            "alert_eligible": self.alert_eligible,
            "listing_classification_flags": self.listing_classification_flags,
            "hard_reject_flags": self.hard_reject_flags,
            "positive_flags": self.positive_flags,
            "risk_flags": self.risk_flags,
        }


def score_listing(
    listing: dict[str, Any],
    repair_values: dict[str, Any],
    *,
    scoring_rules: dict[str, Any] | None = None,
    min_score_to_alert: float | None = None,
    min_profit_to_alert: float | None = None,
    risky_score_range: tuple[float, float] | list[float] | None = None,
    score_threshold: float = 70.0,
    profit_threshold: float = 75.0,
) -> ScoreResult:
    text = _listing_text(listing)
    thresholds = (scoring_rules or {}).get("thresholds", {})
    min_score = float(min_score_to_alert or thresholds.get("min_score_to_alert", score_threshold))
    min_profit = float(min_profit_to_alert or thresholds.get("min_profit_to_alert", profit_threshold))
    risky_range = risky_score_range or thresholds.get("risky_score_range", [35.0, min_score - 0.01])

    hard_flags = _match_flags(text, _patterns_from_rules(scoring_rules, "hard_reject_keywords", HARD_REJECT_PATTERNS))
    if "icloud_locked" in hard_flags and _has_no_icloud_lock_proof(text):
        hard_flags = [flag for flag in hard_flags if flag != "icloud_locked"]
    positive_flags = _match_flags(text, _patterns_from_rules(scoring_rules, "positive_keywords", POSITIVE_PATTERNS))
    risk_flags = _match_flags(text, _patterns_from_rules(scoring_rules, "risk_keywords", RISK_PATTERNS))
    proof_flags = _proof_flags(text, positive_flags)
    if "no_power" in hard_flags or "does_not_turn_on" in hard_flags:
        positive_flags = [flag for flag in positive_flags if flag != "powers_on"]
        proof_flags = [flag for flag in proof_flags if flag != "proof_powers_on"]
    classification = classify_whole_phone_listing(listing, positive_flags)
    hard_flags = _dedupe([*hard_flags, *classification["suppress_flags"]])
    risk_flags = _dedupe([*risk_flags, *classification["risk_flags"]])

    model = "unknown" if classification["suppress_flags"] else detect_model(text, repair_values)
    suspicious_spec_flags = _suspicious_spec_flags(text, model)
    if suspicious_spec_flags:
        classification["flags"] = _dedupe([*classification["flags"], *suspicious_spec_flags])
    if _is_old_ignored_model(text, model):
        hard_flags = _dedupe([*hard_flags, OLD_MODEL_IGNORED_FLAG])
    estimate = _model_estimate(model, repair_values)
    total_cost = float(listing.get("total_cost") or 0)
    estimated_parts_cost, parts_cost_available = _estimate_parts_cost(estimate, positive_flags)
    resale = _resale_estimate(estimate)
    resale_value = resale["mid"]
    resale_value_available = resale_value > 0
    model_has_pricing = model in repair_values
    if model == "unknown" or not model_has_pricing:
        resale_value_available = False
        resale = _empty_resale_estimate()
        resale_value = 0.0
    risk_buffer = float(estimate.get("risk_buffer", repair_values.get("default", {}).get("risk_buffer", 50)))
    effective_min_profit = float(estimate.get("min_profit", min_profit))
    manual_review_allowed = bool(estimate.get("manual_review_allowed", False))
    estimated_profit_available = (
        resale_value_available
        and parts_cost_available
        and classification["has_repair_issue"]
        and classification["has_specific_repair_issue"]
    )
    profit_low, profit_mid, profit_high = _profit_range(
        resale,
        total_cost=total_cost,
        estimated_parts_cost=estimated_parts_cost,
        risk_buffer=risk_buffer,
        available=estimated_profit_available,
    )
    estimated_profit = profit_mid
    parts_pricing_status = str(estimate.get("parts_pricing_status") or "fallback")
    parts_pricing_note = str(estimate.get("parts_pricing_note") or "")
    parts_pricing_label, pricing_warning = _pricing_label_and_warning(
        parts_pricing_status,
        parts_cost_available=parts_cost_available,
        resale_value_available=resale_value_available,
        model_known=model != "unknown" and model_has_pricing,
        has_repair_issue=classification["has_repair_issue"],
        has_specific_repair_issue=classification["has_specific_repair_issue"],
    )
    alert_eligible, alert_ineligible_reasons = _alert_profit_eligibility(
        estimated_profit_available=estimated_profit_available,
        profit_low=profit_low,
        profit_mid=profit_mid,
        profit_high=profit_high,
        min_profit=effective_min_profit,
        parts_pricing_status=parts_pricing_status,
    )
    verification_eligible, verification_reasons = _verification_risk_eligibility(
        total_cost=total_cost,
        resale=resale,
        risk_flags=risk_flags,
        proof_flags=proof_flags,
        suspicious_spec_flags=suspicious_spec_flags,
    )
    if not verification_eligible:
        alert_eligible = False
        alert_ineligible_reasons = _dedupe([*alert_ineligible_reasons, *verification_reasons])

    if hard_flags:
        score = -100.0
        status = "rejected"
    else:
        score = 45.0
        score += min(len(positive_flags) * 12.0, 48.0)
        score -= min(len(risk_flags) * 10.0, 30.0)
        if model != "unknown":
            score += 8.0
        if classification["whole_phone_confidence_passed"]:
            score += 6.0
        else:
            score -= 35.0
        if not classification["has_repair_issue"]:
            score -= 25.0
        score += _profit_score(estimated_profit) if estimated_profit_available else 0.0
        score = max(0.0, min(100.0, score))

        if (
            score >= min_score
            and alert_eligible
            and classification["whole_phone_confidence_passed"]
            and classification["has_repair_issue"]
            and not _has_blocking_risk(risk_flags)
        ):
            status = "candidate"
        elif (
            manual_review_allowed
            and score >= min_score
            and not estimated_profit_available
            and classification["whole_phone_confidence_passed"]
            and classification["has_repair_issue"]
        ):
            status = "risky"
        elif _in_risky_range(score, risky_range) or risk_flags or estimated_profit > 0:
            status = "risky"
        else:
            status = "risky"

    manual_review_reasons = _manual_review_reasons(
        model=model,
        hard_flags=hard_flags,
        risk_flags=risk_flags,
        classification=classification,
        parts_pricing_status=parts_pricing_status,
        parts_cost_available=parts_cost_available,
        estimated_profit_available=estimated_profit_available,
        alert_ineligible_reasons=alert_ineligible_reasons,
    )

    return ScoreResult(
        score=round(score, 2),
        status=status,
        model=model,
        estimated_profit=round(estimated_profit, 2),
        resale_value=round(resale_value, 2),
        resale_low=round(resale["low"], 2),
        resale_mid=round(resale["mid"], 2),
        resale_high=round(resale["high"], 2),
        profit_low=round(profit_low, 2),
        profit_mid=round(profit_mid, 2),
        profit_high=round(profit_high, 2),
        resale_confidence=resale["confidence"],
        resale_sample_size=resale["sample_size"],
        resale_note=resale["note"],
        estimated_parts_cost=round(estimated_parts_cost, 2),
        risk_buffer=round(risk_buffer, 2),
        parts_pricing_status=parts_pricing_status,
        parts_pricing_note=parts_pricing_note,
        parts_pricing_label=parts_pricing_label,
        pricing_warning=pricing_warning,
        estimated_profit_available=estimated_profit_available,
        estimated_parts_cost_available=parts_cost_available,
        manual_review_allowed=manual_review_allowed,
        whole_phone_confidence_passed=classification["whole_phone_confidence_passed"],
        whole_phone_score=classification["whole_phone_score"],
        has_repair_issue=classification["has_repair_issue"],
        manual_review_needed=bool(manual_review_reasons),
        manual_review_reason="; ".join(manual_review_reasons),
        alert_eligible=alert_eligible and status == "candidate",
        listing_classification_flags=classification["flags"],
        hard_reject_flags=hard_flags,
        positive_flags=positive_flags,
        risk_flags=risk_flags,
    )


def classify_whole_phone_listing(listing: dict[str, Any], positive_flags: list[str]) -> dict[str, Any]:
    title = _normalize(str(listing.get("title") or ""))
    condition = _normalize(str(listing.get("condition") or ""))
    category = _listing_category_text(listing)
    suppress_flags = _dedupe([*_match_flags(title, NOT_PHONE_PATTERNS), *_screen_part_suppress_flags(title)])
    flags: list[str] = list(suppress_flags)
    score = 0.0

    if IPHONE_TITLE_PATTERN.search(title):
        score += 2.0
        flags.append("iphone_title")
    if STORAGE_PATTERN.search(title):
        score += 2.0
        flags.append("storage_size")
    if CARRIER_PATTERN.search(title):
        score += 1.0
        flags.append("carrier_or_unlocked")
    if WHOLE_PHONE_CATEGORY_PATTERN.search(category):
        score += 2.0
        flags.append("cell_phone_category")
    if WHOLE_PHONE_CONDITION_PATTERN.search(condition):
        score += 1.0
        flags.append("used_or_parts_condition")

    has_specific_issue = bool(ACTUAL_REPAIR_ISSUE_FLAGS.intersection(positive_flags))
    has_generic_issue = bool(GENERIC_REPAIR_ISSUE_PATTERN.search(title))
    if has_specific_issue or has_generic_issue:
        score += 1.0
        flags.append("repair_issue_in_title")

    if suppress_flags:
        return {
            "flags": _dedupe(flags),
            "suppress_flags": _dedupe(suppress_flags),
            "risk_flags": [],
            "whole_phone_score": score,
            "whole_phone_confidence_passed": False,
            "has_repair_issue": has_specific_issue or has_generic_issue,
            "has_specific_repair_issue": has_specific_issue,
        }

    risk_flags = []
    whole_phone_confidence_passed = score >= 4.0 and bool(IPHONE_TITLE_PATTERN.search(title))
    if not whole_phone_confidence_passed:
        risk_flags.append("not_whole_phone")
        flags.append("not_whole_phone")
    if not (has_specific_issue or has_generic_issue):
        risk_flags.append("no_detected_repair_issue")
        flags.append("no_detected_repair_issue")

    return {
        "flags": _dedupe(flags),
        "suppress_flags": [],
        "risk_flags": risk_flags,
        "whole_phone_score": score,
        "whole_phone_confidence_passed": whole_phone_confidence_passed,
        "has_repair_issue": has_specific_issue or has_generic_issue,
        "has_specific_repair_issue": has_specific_issue,
    }


def detect_model(text: str, repair_values: dict[str, Any]) -> str:
    normalized = _normalize(text)
    candidates = _dedupe([key for key in repair_values if key != "default"] + list(KNOWN_MODEL_ALIASES))
    candidates.sort(key=len, reverse=True)

    for model in candidates:
        pattern = _model_pattern(model)
        if re.search(pattern, normalized):
            return model
    return "unknown"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _listing_text(listing: dict[str, Any]) -> str:
    fields = [
        listing.get("title"),
        listing.get("condition"),
        listing.get("raw_description"),
    ]
    return _normalize(" ".join(str(field) for field in fields if field))


def _listing_category_text(listing: dict[str, Any]) -> str:
    raw = listing.get("category") or listing.get("categories") or ""
    raw_json = listing.get("raw_json") or {}
    summary = raw_json.get("summary") if isinstance(raw_json, dict) else {}
    category_data = []
    if raw:
        category_data.append(raw)
    if isinstance(summary, dict):
        category_data.extend(
            [
                summary.get("categoryPath"),
                summary.get("categoryId"),
                summary.get("categoryName"),
                summary.get("leafCategoryIds"),
                summary.get("categories"),
            ]
        )
    return _normalize(" ".join(str(value) for value in category_data if value))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _match_flags(text: str, patterns: dict[str, str]) -> list[str]:
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _proof_flags(text: str, positive_flags: list[str]) -> list[str]:
    flags = _match_flags(text, PROOF_PATTERNS)
    if "powers_on" in positive_flags:
        flags.append("proof_powers_on")
    if "clean_imei" in positive_flags:
        flags.append("proof_clean_imei")
    if "face_id_works" in positive_flags:
        flags.append("proof_face_id_works")
    return _dedupe(flags)


def _has_no_icloud_lock_proof(text: str) -> bool:
    return bool(re.search(PROOF_PATTERNS["proof_no_icloud_lock"], text, re.IGNORECASE))


def _suspicious_spec_flags(text: str, model: str) -> list[str]:
    flags = []
    if model.startswith("iPhone 13") and IPHONE_13_64GB_PATTERN.search(text):
        flags.append("iphone_13_64gb_mismatch")
    if model.startswith("iPhone 13") and IPHONE_13_5_5IN_PATTERN.search(text):
        flags.append("iphone_13_5_5in_mismatch")
    if flags:
        flags.append("model_spec_mismatch")
    return _dedupe(flags)


def _screen_part_suppress_flags(title: str) -> list[str]:
    if not PARTS_ONLY_PATTERN.search(title) and not SCREEN_PART_LISTING_PATTERN.search(title):
        return []
    if not DISPLAY_PART_PATTERN.search(title):
        return []
    if STORAGE_PATTERN.search(title) or CARRIER_PATTERN.search(title) or FULL_DEVICE_PATTERN.search(title):
        return []

    flags = ["screen_part_not_phone"]
    if re.search(r"\bdigitizer\b", title, re.IGNORECASE):
        flags.append("digitizer_not_phone")
    if re.search(r"\b(?:oled|lcd)\b", title, re.IGNORECASE):
        flags.append("oled_lcd_part_not_phone")
    if re.search(r"\b(?:assembly|display)\b", title, re.IGNORECASE):
        flags.append("display_assembly_not_phone")
    if re.search(r"\b(?:cracked\s+glass|screen\s+glass|glass\s+assembly)\b", title, re.IGNORECASE):
        flags.append("glass_only_not_phone")
    return flags


def _patterns_from_rules(
    scoring_rules: dict[str, Any] | None,
    rule_key: str,
    default_patterns: dict[str, str],
) -> dict[str, str]:
    patterns = dict(default_patterns)
    if not scoring_rules or rule_key not in scoring_rules:
        return patterns

    for flag, keywords in scoring_rules[rule_key].items():
        if isinstance(keywords, str):
            keywords = [keywords]
        rule_pattern = "|".join(_keyword_pattern(keyword) for keyword in keywords)
        patterns[flag] = f"{patterns[flag]}|{rule_pattern}" if flag in patterns else rule_pattern
    return patterns


def _keyword_pattern(keyword: str) -> str:
    if keyword.startswith("regex:"):
        return keyword.removeprefix("regex:")
    escaped = re.escape(keyword.lower()).replace(r"\ ", r"\s+")
    return rf"(?<!\w){escaped}(?!\w)"


def _in_risky_range(score: float, risky_score_range: tuple[float, float] | list[float]) -> bool:
    if len(risky_score_range) != 2:
        return False
    low, high = float(risky_score_range[0]), float(risky_score_range[1])
    return low <= score <= high


def _model_pattern(model: str) -> str:
    escaped = re.escape(model.lower())
    escaped = escaped.replace(r"\ ", r"\s*")
    escaped = escaped.replace("iphone", r"iphone\s*")
    return rf"\b{escaped}\b"


def _model_estimate(model: str, repair_values: dict[str, Any]) -> dict[str, Any]:
    if model in repair_values:
        return repair_values[model]
    return repair_values.get("default", {})


def _resale_estimate(estimate: dict[str, Any]) -> dict[str, Any]:
    resale = estimate.get("resale") if isinstance(estimate.get("resale"), dict) else {}
    fallback_mid = _float_or_none(estimate.get("resale_value"))
    mid = _float_or_none(resale.get("mid"))
    if mid is None:
        mid = fallback_mid or 0.0
    low = _float_or_none(resale.get("low"))
    high = _float_or_none(resale.get("high"))
    if low is None:
        low = mid
    if high is None:
        high = mid
    return {
        "low": low,
        "mid": mid,
        "high": high,
        "confidence": str(resale.get("confidence") or ""),
        "sample_size": int(resale.get("sample_size") or 0),
        "note": str(resale.get("note") or ""),
    }


def _empty_resale_estimate() -> dict[str, Any]:
    return {
        "low": 0.0,
        "mid": 0.0,
        "high": 0.0,
        "confidence": "",
        "sample_size": 0,
        "note": "",
    }


def _profit_range(
    resale: dict[str, Any],
    *,
    total_cost: float,
    estimated_parts_cost: float,
    risk_buffer: float,
    available: bool,
) -> tuple[float, float, float]:
    if not available:
        return 0.0, 0.0, 0.0
    cost_basis = total_cost + estimated_parts_cost + risk_buffer
    return (
        float(resale["low"]) - cost_basis,
        float(resale["mid"]) - cost_basis,
        float(resale["high"]) - cost_basis,
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_old_ignored_model(text: str, model: str) -> bool:
    if OLD_MODEL_PATTERN.search(text):
        return True
    return model in {
        "iPhone 3G",
        "iPhone 3GS",
        "iPhone 4",
        "iPhone 4S",
        "iPhone 5",
        "iPhone 5C",
        "iPhone 5S",
        "iPhone 6",
        "iPhone 6S",
        "iPhone 7",
        "iPhone 8",
    }


def _estimate_parts_cost(estimate: dict[str, Any], positive_flags: list[str]) -> tuple[float, bool]:
    issue_costs = []
    missing_required_cost = False
    for flag, cost_keys in ISSUE_COST_KEYS.items():
        if flag not in positive_flags:
            continue
        cost = _estimate_cost(estimate, cost_keys)
        if cost is None or cost <= 0:
            missing_required_cost = True
            continue
        issue_costs.append(cost)

    if missing_required_cost:
        return sum(issue_costs), False
    if issue_costs:
        return sum(issue_costs), True

    fallback = estimate.get("estimated_parts_cost")
    if fallback is None:
        return 0.0, True
    fallback = float(fallback)
    return fallback, fallback > 0


def _estimate_cost(estimate: dict[str, Any], cost_keys: tuple[str, ...]) -> float | None:
    for key in cost_keys:
        if key.startswith("parts."):
            value = (estimate.get("parts") or {}).get(key.split(".", 1)[1])
        else:
            value = estimate.get(key)
        if value is not None:
            return float(value)
    return None


def _pricing_label_and_warning(
    parts_pricing_status: str,
    *,
    parts_cost_available: bool,
    resale_value_available: bool,
    model_known: bool,
    has_repair_issue: bool,
    has_specific_repair_issue: bool,
) -> tuple[str, str]:
    if not model_known:
        return MODEL_UNKNOWN_LABEL, MODEL_UNKNOWN_WARNING
    if not has_repair_issue:
        return NO_REPAIR_ISSUE_LABEL, NO_REPAIR_ISSUE_WARNING
    if not has_specific_repair_issue:
        return PART_PRICE_MISSING_LABEL, PART_PRICE_OR_ISSUE_MISSING_WARNING
    if not parts_cost_available:
        return PART_PRICE_MISSING_LABEL, PART_PRICE_OR_ISSUE_MISSING_WARNING
    if not resale_value_available:
        return RESALE_MISSING_LABEL, RESALE_MISSING_WARNING
    if parts_pricing_status in VERIFIED_PART_STATUSES:
        return VERIFIED_PARTS_LABEL, VERIFIED_PARTS_LABEL
    if parts_pricing_status in UNVERIFIED_PART_STATUSES:
        return PARTS_ESTIMATE_NOT_VERIFIED_LABEL, PARTS_ESTIMATE_NOT_VERIFIED_LABEL
    return PARTS_ESTIMATE_NOT_VERIFIED_LABEL, PARTS_ESTIMATE_NOT_VERIFIED_LABEL


def _manual_review_reasons(
    *,
    model: str,
    hard_flags: list[str],
    risk_flags: list[str],
    classification: dict[str, Any],
    parts_pricing_status: str,
    parts_cost_available: bool,
    estimated_profit_available: bool,
    alert_ineligible_reasons: list[str] | None = None,
) -> list[str]:
    reasons = []
    reasons.extend(alert_ineligible_reasons or [])
    if classification["suppress_flags"]:
        reasons.append("Accessory/part listing")
    if any(flag in classification["suppress_flags"] for flag in (
        "screen_part_not_phone",
        "display_assembly_not_phone",
        "digitizer_not_phone",
        "oled_lcd_part_not_phone",
        "glass_only_not_phone",
    )):
        reasons.append("Screen/display part listing")
    if not classification["whole_phone_confidence_passed"]:
        reasons.append("Not a whole phone")
    if not classification["has_repair_issue"]:
        reasons.append("No specific repair issue detected")
    elif not classification["has_specific_repair_issue"]:
        reasons.append("Parts-only ambiguous")
    if not parts_cost_available:
        reasons.append("Missing part price")
    if parts_pricing_status in UNVERIFIED_PART_STATUSES:
        reasons.append("Parts estimate not verified")
    if parts_pricing_status == "verified_screenshot_low_confidence":
        reasons.append("Low-confidence pricing")
    if model == "unknown":
        reasons.append("Model unknown")
    if "read_description" in risk_flags:
        reasons.append("Read description listing")
    return _dedupe(reasons)


def _alert_profit_eligibility(
    *,
    estimated_profit_available: bool,
    profit_low: float,
    profit_mid: float,
    profit_high: float,
    min_profit: float,
    parts_pricing_status: str,
) -> tuple[bool, list[str]]:
    if not estimated_profit_available:
        return False, []

    reasons = []
    if profit_mid <= 0:
        reasons.append("Expected profit below threshold")
        return False, reasons
    if profit_mid < min_profit:
        reasons.append("Expected profit below threshold")
    if profit_high > 0 and profit_mid < min_profit:
        reasons.append("Only optimistic profit clears threshold")
    if profit_low < 0 and profit_mid < min_profit * 1.5:
        reasons.append("Conservative profit is negative")
    if parts_pricing_status in STRICT_MARGIN_PART_STATUSES and profit_mid < min_profit * 1.75:
        reasons.append("Low-confidence pricing needs stronger profit")

    return not reasons, _dedupe(reasons)


def _verification_risk_eligibility(
    *,
    total_cost: float,
    resale: dict[str, Any],
    risk_flags: list[str],
    proof_flags: list[str],
    suspicious_spec_flags: list[str],
) -> tuple[bool, list[str]]:
    reasons = []
    if suspicious_spec_flags:
        reasons.append("Model/spec mismatch")

    resale_floor = float(resale.get("low") or resale.get("mid") or 0)
    if (
        resale_floor > 0
        and total_cost > 0
        and total_cost < resale_floor * 0.30
        and {"for_parts", "read_description"}.intersection(risk_flags)
        and not proof_flags
    ):
        reasons.append("Too cheap without proof")
        reasons.append("Parts-only listing lacks power/iCloud/IMEI proof")

    return not reasons, reasons


def _has_blocking_risk(risk_flags: list[str]) -> bool:
    return bool(BLOCKING_RISK_FLAGS.intersection(risk_flags))


def _profit_score(estimated_profit: float) -> float:
    if estimated_profit >= 250:
        return 25.0
    if estimated_profit >= 150:
        return 18.0
    if estimated_profit >= 75:
        return 10.0
    if estimated_profit > 0:
        return 3.0
    return -15.0
