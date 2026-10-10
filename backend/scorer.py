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
    "board_damage": r"\bboard\s+damage(?:d)?\b",
    "motherboard_issue": r"\bmother\s*board\s+(?:issue|problem|damage(?:d)?)\b",
    "logic_issue": r"\blogic\s+(?:issue|problem)\b",
    "bent_frame": r"\bbent\s+frame\b",
    "major_frame_damage": r"\bmajor\s+frame\s+damage\b|\bframe\s+(?:is\s+)?(?:badly\s+)?damaged\b",
    "no_power": r"\bno\s+power\b|\bdoes\s+not\s+power\s+on\b|\bdoesn'?t\s+power\s+on\b|\bwon'?t\s+power\s+on\b|\bnot\s+powering\s+on\b",
    "does_not_turn_on": r"\bdoes\s+not\s+turn\s+on\b|\bdoesn'?t\s+turn\s+on\b|\bwon'?t\s+turn\s+on\b",
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
    "replacement_part_not_phone": r"\b(?:replacement\s+part|repair\s+part|flex\s+cable|for\s+flex\s+parts\s+only|flex\s+parts\s+only)\b",
    "logic_board": r"\blogic\s+board\b",
    "motherboard": r"\bmother\s*board\b",
}

POSITIVE_PATTERNS = {
    "cracked_screen": r"\bcracked\s+screen\b|\bscreen\s+(?:is\s+)?cracked\b",
    "screen_display_issue": r"\b(?:bad\s+screen|screen\s+bad|screen\s+lines|vertical\s+lines|black\s+spot|no\s+image|no\s+display|black\s+screen|bad\s+lcd|damaged\s+lcd|lcd\s+screen(?:\s+damaged)?|screen\s+has\s+lines|display\s+lines)\b",
    "bad_battery": r"\bbad\s+battery\b|\bswollen\s+battery\b|\bbattery\s+(?:service|needs\s+replacement|issue)\b|\bbattery\s+needs\s+to\s+be\s+serviced\b",
    "back_glass_cracked": r"\bback\s+glass\s+cracked\b|\bcracked\s+back\s+glass\b|\bcracked\s+back\b",
    "camera_lens_cracked": r"\bcamera\s+lens\s+cracked\b|\bcracked\s+camera\s+lens\b|\b(?:camera\s+glass|camera\s+lens)\s+(?:broken|cracked)\b|\bbroken\s+(?:camera\s+glass|camera\s+lens)\b",
    "charging_port_issue": r"\b(?:charging|charge)(?:\s+port)?\s+(?:issue|problem|bad|broken|fault)\b|\b(?:does\s+not|doesn'?t|won'?t)\s+charge\b",
    "camera_fault": r"\b(?:front|rear|main|selfie)?\s*camera\s+(?:issue|problem|bad|broken|fault|not\s+working|doesn'?t\s+work)\b|\bbad\s+(?:(?:front|rear|main|selfie)\s+)?camera\b",
    "face_id_issue": r"\bface\s*id\s+(?:issue|problem|not\s+working|does\s+not\s+work|doesn'?t\s+work|broken|fail(?:ed|s)?)\b|\bbad\s+face\s*id\b",
    "digitizer_issue": r"\b(?:touch|digitizer)\s+(?:issue|problem|not\s+working|does\s+not\s+work|doesn'?t\s+work|broken|unresponsive)\b",
    "bad_oled": r"\bbad\s+oled\b",
    "powers_on": r"\bpowers?\s+on\b|\bturns?\s+on\b|\bboots?\b|\bphone\s+works\b|\bdoes\s+still\s+work\b",
    "unlocked": r"\bfactory\s+unlocked\b|\bcarrier\s+unlocked\b|\bunlocked\b",
    "clean_imei": r"\bclean\s+imei\b|\bclean\s+esn\b|\bready\s+to\s+be\s+activated\b",
    "face_id_works": r"\bface\s*id\s+works\b|\bface\s*id\s+working\b|\bface\s*id\s+functions?\s+properly\b|\bface\s*id\s+works?\s+as\s+expected\b",
}

PROOF_PATTERNS = {
    "proof_tested": r"\btested\b",
    "proof_icloud_off": r"\bicloud\s+(?:off|removed|signed\s+out)\b|\bfmi\s+(?:off|removed)\b",
    "proof_no_icloud_lock": r"\bno\s+icloud\s+lock(?:ed)?\b|\bnot\s+icloud\s+lock(?:ed)?\b",
    "proof_touch_works": r"\btouch\s+(?:works|working|is\s+working)\b",
    "proof_display_works": r"\bdisplay\s+(?:works|working|is\s+working)\b|\blcd\s+(?:works|working|is\s+working|functional)\b|\boled\s+(?:works|working|is\s+working|functional)\b|\blcd/oled\s+(?:has\s+no\s+issues|no\s+issues|fully\s+functional)\b",
    "proof_fully_functional_except_issue": r"\bfully\s+functional\s+except\b|\beverything\s+works\s+except\b|\beverything\s+else\s+remains\s+functional\b|\bonly\s+issue\s+is\b",
}

RISK_PATTERNS = {
    "read_description": r"\bread\s+description\b",
    "for_parts": r"\bfor\s+parts\b|\bparts\s+only\b",
    "as_is": r"\bas[-\s]?is\b",
    "untested": r"\buntested\b",
    "unknown_issue": r"\bunknown\s+(?:issue|problem|condition)\b",
    "multiple_issues": r"\bmultiple\s+issues\b",
    "ic_issue": r"\b(?:no\s+ic|bad\s+ic|ic\s+(?:issue|problem|bad))\b",
    "no_ic_read": r"\bno\s+ic\s+read\b",
    "ic_read": r"\bic\s+read\b",
    "not_original_owner": r"\bnot\s+(?:the\s+)?original\s+owner\b|\bno\s+original\s+owner\b",
    "unknown_icloud": r"\bunknown\s+icloud\b|\bcannot\s+verify\s+icloud\b",
    "face_id_unknown": r"\bface\s*id\s+unknown\b",
    "touch_not_working": r"\btouch\s+(?:not\s+working|does\s+not\s+work|doesn'?t\s+work)\b",
    "display_not_original": r"\bdisplay\s+(?:is\s+)?not\s+original\b|\bnon[-\s]?original\s+display\b",
    "parts_swapped": r"\bparts?\s+swapped\b",
}

ISSUE_COST_KEYS = {
    "cracked_screen": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
    "screen_display_issue": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
    "bad_oled": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
    "bad_battery": ("parts.battery", "battery_cost"),
    "back_glass_cracked": ("parts.back_glass", "back_glass_cost"),
    "camera_lens_cracked": ("parts.camera_lens", "camera_lens_cost"),
    "charging_port_issue": ("parts.charging_port", "charging_port_cost"),
    "camera_fault": ("parts.camera", "parts.rear_camera", "camera_cost"),
    "face_id_issue": ("parts.face_id", "face_id_cost"),
    "digitizer_issue": ("parts.screen_safe", "parts.screen_budget", "screen_cost"),
}

VERIFIED_PART_STATUSES = {"verified_screenshot", "verified_screenshot_and_page"}
UNVERIFIED_PART_STATUSES = {"estimated", "verified_screenshot_low_confidence", "fallback", "manual_part_update"}
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
    "camera_fault",
    "face_id_issue",
    "digitizer_issue",
}

OLD_MODEL_IGNORED_FLAG = "old_model_ignored"

BLOCKING_RISK_FLAGS = {
    "untested",
    "unknown_issue",
    "multiple_issues",
    "ic_issue",
    "no_ic_read",
    "ic_read",
    "not_original_owner",
    "unknown_icloud",
    "face_id_unknown",
    "touch_not_working",
    "display_not_original",
    "parts_swapped",
}

GENERIC_REPAIR_ISSUE_PATTERN = re.compile(
    r"\b(?:broken|for\s+repair|parts\s+only|for\s+parts|read\s+description|parts\s*/\s*repair)\b",
    re.IGNORECASE,
)

STORAGE_PATTERN = re.compile(r"\b(?:(64|128|256|512)\s*gb|(1)\s*tb)\b", re.IGNORECASE)
STORAGE_ORDER = ("64GB", "128GB", "256GB", "512GB", "1TB")
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
HIGH_CONFIDENCE_SCREEN_COMPONENT_PATTERN = re.compile(
    r"\b(?:"
    r"screen\s+display\s+assembly|screen\s+assembly|display\s+assembly|lcd\s+assembly|oled\s+assembly|"
    r"oled\s+only|lcd\s+only|screen\s+only|display\s+only|"
    r"replacement\s+(?:screen|display)|digitizer\s+assembly|front\s+glass\s+assembly|"
    r"oem\s+(?:screen|display)|original\s+(?:screen|display)|"
    r"(?:screen|display)\s+for\s+iphone|compatible\s+with\s+iphone|"
    r"parts\s+only\s+screen|screen\s+replacement\s+part|good\s+(?:oled|lcd)\s+touch\s+works|"
    r"(?:oled\s+)?lcd\s+screen|(?:oled|lcd|display|screen)\s+(?:works|tested\s+working)"
    r")\b",
    re.IGNORECASE,
)
WHOLE_PHONE_PROOF_PATTERN = re.compile(
    r"\b(?:phone\s+works|device\s+works|works\s+great|fully\s+functional|tested\s+and\s+functional|powers?\s+on|turns?\s+on|boots?|clean\s+imei|clean\s+esn|ready\s+to\s+be\s+activated|face\s*id|icloud\s+(?:off|removed|signed\s+out)|fmi\s+off|no\s+icloud\s+lock)\b",
    re.IGNORECASE,
)
FULL_DEVICE_PATTERN = re.compile(r"\b(?:phone|device|works|powers?\s+on|turns?\s+on|clean\s+imei|face\s*id)\b", re.IGNORECASE)
COMPONENT_REJECT_PATTERN = re.compile(
    r"\b(?:"
    r"phone\s+(?:is\s+)?not\s+included|device\s+(?:is\s+)?not\s+included|"
    r"screen\s+only|display\s+only|oled\s+only|lcd\s+only|"
    r"screen\s+display\s+assembly|display\s+assembly|screen\s+assembly|"
    r"for\s+flex\s+parts\s+only|flex\s+parts\s+only|"
    r"replacement\s+screen|replacement\s+display|screen\s+for\s+iphone|"
    r"housing\s+only|box\s+only|empty\s+box|back\s+glass\s+part\s+only|camera\s+only|(?<!bad\s)(?<!weak\s)battery\s+only"
    r")\b",
    re.IGNORECASE,
)
NORMAL_NOT_INCLUDED_ACCESSORY_PATTERN = re.compile(
    r"\b(?:not\s+included|what'?s\s+not\s+included)\s*:?\s*(?:[-\s/]*(?:sim\s+card|charger|headphones?|original\s+box|box)){1,5}\b|"
    r"\bno\s+(?:sim\s+card|charger|headphones?|box)\b|"
    r"\bno\s+box\s+or\s+anything\s+else\s+included\b|"
    r"\bpower\s+cables?\s+or\s+other\s+accessories\b",
    re.IGNORECASE,
)
OLD_MODEL_PATTERN = re.compile(r"\biphone\s*(?:3g|3gs|4s?|5c|5s|5|6s?|7|8)(?:\s+plus)?\b", re.IGNORECASE)
IPHONE_13_64GB_PATTERN = re.compile(r"\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b.*\b64\s*gb\b|\b64\s*gb\b.*\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b", re.IGNORECASE)
IPHONE_13_5_5IN_PATTERN = re.compile(r"\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b.*\b5\.5\s*(?:in|inch|inches)\b|\b5\.5\s*(?:in|inch|inches)\b.*\biphone\s*13(?:\s+(?:mini|pro|max)){0,3}\b", re.IGNORECASE)
KNOWN_MODEL_ALIASES = (
    "iPhone 17 Air",
    "iPhone 17 Pro Max",
    "iPhone 17 Pro",
    "iPhone 17",
    "iPhone 16 Pro Max",
    "iPhone 16 Pro",
    "iPhone 16 Plus",
    "iPhone 16",
    "iPhone 15 Pro Max",
    "iPhone 15 Pro",
    "iPhone 15 Plus",
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
    resale_source: str = "missing"
    resale_market_source: str = "missing"
    resale_condition_used: str = ""
    resale_storage_used: str | None = None
    storage_resale_warning: str = ""
    mint_resale_low: float = 0.0
    mint_resale_mid: float = 0.0
    mint_resale_high: float = 0.0
    mint_profit_low: float = 0.0
    mint_profit_mid: float = 0.0
    mint_profit_high: float = 0.0
    estimated_selling_fees: float = 0.0
    estimated_outbound_shipping: float = 0.0
    exit_cost_marketplace: str = ""
    exit_cost_note: str = ""
    storage_capacity: str | None = None
    storage_confidence: str = ""
    storage_source: str = ""
    parts_pricing_status: str = "fallback"
    parts_pricing_note: str = ""
    parts_pricing_label: str = PARTS_ESTIMATE_NOT_VERIFIED_LABEL
    pricing_warning: str = PARTS_ESTIMATE_NOT_VERIFIED_LABEL
    estimated_profit_available: bool = True
    estimated_parts_cost_available: bool = True
    manual_review_allowed: bool = False
    whole_phone_confidence_passed: bool = False
    whole_phone_score: float = 0.0
    item_type: str = "ambiguous"
    item_type_reason: str = ""
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
            "resale_source": self.resale_source,
            "resale_market_source": self.resale_market_source,
            "resale_condition_used": self.resale_condition_used,
            "resale_storage_used": self.resale_storage_used,
            "storage_resale_warning": self.storage_resale_warning,
            "mint_resale_low": self.mint_resale_low,
            "mint_resale_mid": self.mint_resale_mid,
            "mint_resale_high": self.mint_resale_high,
            "mint_profit_low": self.mint_profit_low,
            "mint_profit_mid": self.mint_profit_mid,
            "mint_profit_high": self.mint_profit_high,
            "storage_capacity": self.storage_capacity,
            "storage_confidence": self.storage_confidence,
            "storage_source": self.storage_source,
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
            "item_type": self.item_type,
            "item_type_reason": self.item_type_reason,
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
    resale_research: dict[str, Any] | None = None,
    scoring_rules: dict[str, Any] | None = None,
    min_score_to_alert: float | None = None,
    min_profit_to_alert: float | None = None,
    risky_score_range: tuple[float, float] | list[float] | None = None,
    forced_model: str | None = None,
    forced_storage_capacity: str | None = None,
    forced_issue_type: str | None = None,
    forced_part_cost: float | None = None,
    score_threshold: float = 70.0,
    profit_threshold: float = 75.0,
) -> ScoreResult:
    text = _listing_text(listing)
    description_signals = extract_description_signals(listing)
    thresholds = (scoring_rules or {}).get("thresholds", {})
    min_score = float(min_score_to_alert or thresholds.get("min_score_to_alert", score_threshold))
    min_profit = float(min_profit_to_alert or thresholds.get("min_profit_to_alert", profit_threshold))
    risky_range = risky_score_range or thresholds.get("risky_score_range", [35.0, min_score - 0.01])

    hard_flags = _match_flags(text, _patterns_from_rules(scoring_rules, "hard_reject_keywords", HARD_REJECT_PATTERNS))
    if "icloud_locked" in hard_flags and _has_no_icloud_lock_proof(text):
        hard_flags = [flag for flag in hard_flags if flag != "icloud_locked"]
    positive_flags = _match_flags(text, _patterns_from_rules(scoring_rules, "positive_keywords", POSITIVE_PATTERNS))
    positive_flags = _dedupe([*positive_flags, *_positive_flags_from_description_signals(description_signals)])
    risk_flags = _match_flags(text, _patterns_from_rules(scoring_rules, "risk_keywords", RISK_PATTERNS))
    forced_issue_flag = _issue_flag_from_override(forced_issue_type)
    if forced_issue_flag:
        positive_flags = _dedupe([*positive_flags, forced_issue_flag])
    proof_flags = _proof_flags(text, positive_flags)
    if "no_power" in hard_flags or "does_not_turn_on" in hard_flags:
        positive_flags = [flag for flag in positive_flags if flag != "powers_on"]
        proof_flags = [flag for flag in proof_flags if flag != "proof_powers_on"]
    classification = classify_whole_phone_listing(listing, positive_flags, description_signals=description_signals)
    hard_flags = _dedupe([*hard_flags, *classification["suppress_flags"]])
    risk_flags = _dedupe([*risk_flags, *classification["risk_flags"]])

    model = (
        str(forced_model or "").strip()
        or ("unknown" if classification["suppress_flags"] else detect_model_from_listing(listing, repair_values))
    )
    suspicious_spec_flags = _suspicious_spec_flags(text, model)
    if suspicious_spec_flags:
        classification["flags"] = _dedupe([*classification["flags"], *suspicious_spec_flags])
    if _is_old_ignored_model(text, model):
        hard_flags = _dedupe([*hard_flags, OLD_MODEL_IGNORED_FLAG])
    estimate_source_model = _model_estimate_source(model, repair_values)
    estimate = _model_estimate(model, repair_values)
    storage = detect_storage(listing, forced_storage_capacity=forced_storage_capacity)
    total_cost = float(listing.get("total_cost") or 0)
    estimated_parts_cost, parts_cost_available = _estimate_parts_cost(
        estimate,
        positive_flags,
        forced_issue_type=forced_issue_type,
        forced_part_cost=forced_part_cost,
    )
    fallback_parts_sources: list[str] = []
    if not parts_cost_available and classification["has_specific_repair_issue"]:
        fallback_parts_cost, fallback_parts_sources = _estimate_parts_cost_from_nearest_models(
            model,
            repair_values,
            positive_flags,
            forced_issue_type=forced_issue_type,
        )
        if fallback_parts_cost > 0:
            estimated_parts_cost = fallback_parts_cost
            parts_cost_available = True
    resale = _resale_estimate(
        estimate,
        storage["storage_capacity"],
        model=model,
        pricing_model=estimate_source_model,
        resale_research=resale_research,
    )
    resale_value = resale["mid"]
    resale_value_available = resale_value > 0
    model_has_pricing = estimate_source_model is not None
    if model == "unknown" or not model_has_pricing:
        resale_value_available = False
        resale = _empty_resale_estimate()
        resale_value = 0.0
    if classification["item_type"] != "whole_phone":
        # A component or an unresolved item cannot inherit complete-handset resale.
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
    exit_cost_model = _exit_cost_model(resale_research)
    profit_low, profit_mid, profit_high = _profit_range(
        resale,
        total_cost=total_cost,
        estimated_parts_cost=estimated_parts_cost,
        risk_buffer=risk_buffer,
        available=estimated_profit_available,
        exit_cost_model=exit_cost_model,
    )
    mint_profit_low, mint_profit_mid, mint_profit_high = _profit_range(
        resale["mint"],
        total_cost=total_cost,
        estimated_parts_cost=estimated_parts_cost,
        risk_buffer=risk_buffer,
        available=estimated_profit_available and float(resale["mint"].get("mid") or 0) > 0,
        exit_cost_model=exit_cost_model,
    )
    mid_exit_costs = _estimated_exit_costs(float(resale.get("mid") or 0), exit_cost_model)
    estimated_profit = profit_mid
    parts_pricing_status = str(estimate.get("parts_pricing_status") or "fallback")
    parts_pricing_note = str(estimate.get("parts_pricing_note") or "")
    if estimate_source_model and estimate_source_model != model:
        parts_pricing_status = "estimated"
        parts_pricing_note = _join_note(
            parts_pricing_note,
            f"Exact {model} pricing unavailable; estimated from {estimate_source_model}.",
        )
    if fallback_parts_sources:
        parts_pricing_status = "estimated"
        parts_pricing_note = _join_note(
            parts_pricing_note,
            "Missing repair price estimated conservatively from " + ", ".join(fallback_parts_sources) + ".",
        )
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
    if not alert_eligible and estimated_profit_available and mint_profit_high >= effective_min_profit:
        alert_ineligible_reasons = _dedupe([*alert_ineligible_reasons, "Profit depends on mint resale"])
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
    model_verification_eligible, model_verification_reasons = _high_resale_model_verification_eligibility(
        listing=listing,
        model=model,
        storage_capacity=storage["storage_capacity"],
        resale=resale,
        profit_mid=profit_mid,
        parts_pricing_status=parts_pricing_status,
        estimated_profit_available=estimated_profit_available,
        proof_flags=proof_flags,
    )
    if not model_verification_eligible:
        alert_eligible = False
        alert_ineligible_reasons = _dedupe([*alert_ineligible_reasons, *model_verification_reasons])
    storage_verification_eligible, storage_verification_reasons = _storage_unknown_verification_eligibility(
        storage_capacity=storage["storage_capacity"],
        resale=resale,
        proof_flags=proof_flags,
        estimated_profit_available=estimated_profit_available,
    )
    if not storage_verification_eligible:
        alert_eligible = False
        alert_ineligible_reasons = _dedupe([*alert_ineligible_reasons, *storage_verification_reasons])

    if hard_flags:
        score = -100.0
        status = "rejected"
    else:
        score = 45.0
        score += min(len(positive_flags) * 12.0, 48.0)
        penalty_risk_flags = _risk_flags_for_score(
            risk_flags,
            proof_flags=proof_flags,
            hard_flags=hard_flags,
            classification=classification,
            estimated_profit_available=estimated_profit_available,
            profit_mid=profit_mid,
            min_profit=effective_min_profit,
        )
        score -= min(len(penalty_risk_flags) * 10.0, 30.0)
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
        storage_resale_warning=resale["storage_warning"],
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
        resale_source=resale["source"],
        resale_market_source=resale["market_source"],
        resale_condition_used=resale["condition_used"],
        resale_storage_used=resale["storage_used"],
        storage_resale_warning=resale["storage_warning"],
        mint_resale_low=round(resale["mint"]["low"], 2),
        mint_resale_mid=round(resale["mint"]["mid"], 2),
        mint_resale_high=round(resale["mint"]["high"], 2),
        mint_profit_low=round(mint_profit_low, 2),
        mint_profit_mid=round(mint_profit_mid, 2),
        mint_profit_high=round(mint_profit_high, 2),
        estimated_selling_fees=round(mid_exit_costs["selling_fees"], 2),
        estimated_outbound_shipping=round(mid_exit_costs["outbound_shipping"], 2),
        exit_cost_marketplace=str(exit_cost_model.get("marketplace") or "") if exit_cost_model else "",
        exit_cost_note=str(exit_cost_model.get("note") or "") if exit_cost_model else "",
        storage_capacity=storage["storage_capacity"],
        storage_confidence=storage["storage_confidence"],
        storage_source=storage["storage_source"],
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
        item_type=classification["item_type"],
        item_type_reason=classification["item_type_reason"],
        has_repair_issue=classification["has_repair_issue"],
        manual_review_needed=bool(manual_review_reasons),
        manual_review_reason="; ".join(manual_review_reasons),
        alert_eligible=alert_eligible and status == "candidate",
        listing_classification_flags=classification["flags"],
        hard_reject_flags=hard_flags,
        positive_flags=positive_flags,
        risk_flags=risk_flags,
    )


def classify_whole_phone_listing(
    listing: dict[str, Any],
    positive_flags: list[str],
    *,
    description_signals: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    title = _normalize(str(listing.get("title") or ""))
    description = _normalize(str(listing.get("raw_description") or ""))
    condition = _normalize(str(listing.get("condition") or ""))
    category = _listing_category_text(listing)
    description_signals = description_signals or extract_description_signals(listing)
    handset_context = bool(
        (STORAGE_PATTERN.search(title) or CARRIER_PATTERN.search(title))
        and (CLEAR_HANDSET_DAMAGE_PATTERN.search(title)
             or "back_glass_cracked" in positive_flags
             or description_signals.get("included_device_signals"))
    )
    component_from_description = (
        [] if handset_context and description_signals.get("included_device_signals")
        else _description_component_suppress_flags(title, description, description_signals)
    )
    suppress_flags = _dedupe(
        [
            *_match_flags(title, NOT_PHONE_PATTERNS),
            *([] if handset_context else _screen_part_suppress_flags(title)),
            *([] if handset_context else _structural_component_suppress_flags(title)),
            *component_from_description,
        ]
    )
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
    if description_signals.get("included_device_signals") or description_signals.get("whole_phone_evidence"):
        score += 2.0
        flags.append("description_whole_phone_evidence")
    if description_signals.get("functionality_signals") or description_signals.get("clean_activation_signals"):
        score += 1.0
        flags.append("description_functionality_evidence")
    if description_signals.get("normal_not_included_accessory_list"):
        flags.append("normal_accessory_exclusions")

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
            "item_type": "component",
            "item_type_reason": suppress_flags[0],
            "has_repair_issue": has_specific_issue or has_generic_issue,
            "has_specific_repair_issue": has_specific_issue,
        }

    risk_flags = []
    handset_evidence = _clear_handset_evidence(title, positive_flags, description_signals)
    whole_phone_confidence_passed = score >= 4.0 and bool(IPHONE_TITLE_PATTERN.search(title)) and handset_evidence
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
        "item_type": "whole_phone" if handset_evidence else "ambiguous",
        "item_type_reason": "handset_evidence" if handset_evidence else "insufficient_whole_phone_evidence",
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


def detect_model_from_listing(listing: dict[str, Any], repair_values: dict[str, Any]) -> str:
    """Prefer listing-owned fields so seller templates cannot contaminate model detection."""
    title_model = detect_model(str(listing.get("title") or ""), repair_values)
    if title_model != "unknown":
        return title_model

    structured_text = " ".join(_collect_modelish_values(listing.get("raw_json") or {}))
    for key in ("aspects", "localizedAspects", "itemSpecifics"):
        structured_text = f"{structured_text} {' '.join(_collect_modelish_values(listing.get(key) or {}))}"
    structured_model = detect_model(structured_text, repair_values)
    if structured_model != "unknown":
        return structured_model

    description = str(listing.get("raw_description") or "")
    candidates = _detected_models(description, repair_values)
    return candidates[0] if len(candidates) == 1 else "unknown"


def _detected_models(text: str, repair_values: dict[str, Any]) -> list[str]:
    normalized = _normalize(text)
    candidates = _dedupe([key for key in repair_values if key != "default"] + list(KNOWN_MODEL_ALIASES))
    candidates.sort(key=len, reverse=True)
    matches: list[str] = []
    occupied_spans: list[tuple[int, int]] = []
    for model in candidates:
        for match in re.finditer(_model_pattern(model), normalized):
            start, end = match.span()
            if any(start >= occupied_start and end <= occupied_end for occupied_start, occupied_end in occupied_spans):
                continue
            matches.append(model)
            occupied_spans.append((start, end))
            break
    return matches


def detect_storage(listing: dict[str, Any], *, forced_storage_capacity: str | None = None) -> dict[str, str | None]:
    if forced_storage_capacity:
        return {
            "storage_capacity": str(forced_storage_capacity).strip(),
            "storage_confidence": "manual_override",
            "storage_source": "user_item_correction",
        }
    title_match = _storage_from_text(str(listing.get("title") or ""))
    if title_match:
        return {
            "storage_capacity": title_match,
            "storage_confidence": "high",
            "storage_source": "title",
        }

    aspect_text = _storage_aspect_text(listing)
    aspect_match = _storage_from_text(aspect_text)
    if aspect_match:
        return {
            "storage_capacity": aspect_match,
            "storage_confidence": "medium",
            "storage_source": "item_aspects",
        }

    return {
        "storage_capacity": None,
        "storage_confidence": "",
        "storage_source": "",
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _listing_text(listing: dict[str, Any]) -> str:
    fields = [
        listing.get("title"),
        listing.get("condition"),
        listing.get("raw_description"),
        _listing_signal_aspect_text(listing),
    ]
    return _normalize(" . ".join(str(field) for field in fields if field))


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


def _storage_aspect_text(listing: dict[str, Any]) -> str:
    raw_json = listing.get("raw_json") or {}
    values = []
    if isinstance(raw_json, dict):
        values.extend(_collect_storageish_values(raw_json))
    for key in ("aspects", "localizedAspects", "itemSpecifics"):
        value = listing.get(key)
        if value:
            values.extend(_collect_storageish_values(value))
    return " ".join(str(value) for value in values if value)


def _listing_signal_aspect_text(listing: dict[str, Any]) -> str:
    raw_json = listing.get("raw_json") or {}
    values = []
    if isinstance(raw_json, dict):
        values.extend(_collect_signal_values(raw_json))
    for key in ("aspects", "localizedAspects", "itemSpecifics"):
        value = listing.get(key)
        if value:
            values.extend(_collect_signal_values(value))
    return " ".join(str(value) for value in values if value)


def _collect_signal_values(value: Any, parent_key: str = "") -> list[str]:
    values: list[str] = []
    signal_key = bool(
        re.search(
            r"\b(?:shortdescription|conditiondescription|aspect|model|network|lock|carrier|battery|storage|included|features?|screen|camera|imei|esn|icloud|fmi|charge|touch|oled|lcd|condition)\b",
            parent_key,
            re.IGNORECASE,
        )
    )
    if isinstance(value, dict):
        name = value.get("name")
        nested_value = value.get("value")
        if name is not None and nested_value is not None:
            values.append(f"{name}: {nested_value}")
        for key, nested in value.items():
            values.extend(_collect_signal_values(nested, str(key)))
        return values
    if isinstance(value, list):
        for nested in value:
            values.extend(_collect_signal_values(nested, parent_key))
        return values
    if value is None:
        return values
    text = str(value)
    if signal_key or _signal_text_is_useful(text):
        values.append(text)
    return values


def _signal_text_is_useful(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:iphone|unlocked|clean\s+imei|clean\s+esn|ready\s+to\s+be\s+activated|face\s*id|battery\s+health|powers?\s+on|phone\s+works|does\s+still\s+work|fully\s+functional|touch|digitizer|lcd|oled|camera|charge\s+port|included|not\s+included|cracked|bad\s+battery|weak\s+battery|non[-\s]?oem)\b",
            text,
            re.IGNORECASE,
        )
    )


def extract_description_signals(listing: dict[str, Any]) -> dict[str, list[str]]:
    title = str(listing.get("title") or "")
    raw_description = str(listing.get("raw_description") or "")
    aspect_text = _listing_signal_aspect_text(listing)
    text = _normalize(" . ".join(part for part in (title, raw_description, aspect_text) if part))
    description_text = _normalize(" . ".join(part for part in (raw_description, aspect_text) if part))
    signals = {
        "whole_phone_evidence": _whole_phone_evidence_signals(text),
        "functionality_signals": _functionality_signals(text),
        "included_device_signals": _included_device_signals(text),
        "normal_not_included_accessory_list": _normal_not_included_accessory_signals(description_text),
        "clean_activation_signals": _clean_activation_signals(text),
        "repair_detail_signals": _repair_detail_signals(text),
        "component_reject_signals": _component_reject_signals(text),
    }
    signals = {key: _dedupe(values) for key, values in signals.items()}
    if (
        "device_not_included" in signals["component_reject_signals"]
        and signals["included_device_signals"]
        and signals["normal_not_included_accessory_list"]
    ):
        signals["component_reject_signals"] = [
            signal for signal in signals["component_reject_signals"] if signal != "device_not_included"
        ]
    return signals


def _positive_flags_from_description_signals(signals: dict[str, list[str]]) -> list[str]:
    flags: list[str] = []
    repair = set(signals.get("repair_detail_signals") or [])
    functionality = set(signals.get("functionality_signals") or [])
    activation = set(signals.get("clean_activation_signals") or [])
    if repair.intersection({"cracked_screen"}):
        flags.append("cracked_screen")
    if repair.intersection({"cracked_back", "cracked_back_glass"}):
        flags.append("back_glass_cracked")
    if repair.intersection({"bad_battery", "service_battery", "swollen_battery"}):
        flags.append("bad_battery")
    if repair.intersection({"bad_lcd", "non_oem_screen"}):
        flags.append("screen_display_issue")
    if repair.intersection({"bad_oled"}):
        flags.append("bad_oled")
    if repair.intersection({"charging_port_issue"}):
        flags.append("charging_port_issue")
    if repair.intersection({"camera_fault"}):
        flags.append("camera_fault")
    if repair.intersection({"face_id_issue"}):
        flags.append("face_id_issue")
    if repair.intersection({"digitizer_issue"}):
        flags.append("digitizer_issue")
    if functionality.intersection({"powers_on", "boots", "phone_works", "fully_functional", "tested_functional", "everything_else_functional"}):
        flags.append("powers_on")
    if functionality.intersection({"face_id_works"}):
        flags.append("face_id_works")
    if activation.intersection({"clean_imei", "clean_esn", "ready_to_activate"}):
        flags.append("clean_imei")
    return _dedupe(flags)


def _included_device_signals(text: str) -> list[str]:
    patterns = {
        "items_included_iphone": r"\bitems\s+included\s+in\s+this\s+sale\s*:?\s*.{0,180}\b(?:apple\s+)?iphone\b",
        "whats_included_phone": r"\bwhat'?s\s+included\s*:?\s*-?\s*phone\b",
        "included_device": r"\bincluded\s*:?\s*device\b|\bdevice\s+included\b",
        "iphone_only": r"\biphone\s+only\b",
        "phone_only": r"\bphone\s+only\b",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _whole_phone_evidence_signals(text: str) -> list[str]:
    patterns = {
        "iphone_title_or_specs": r"\b(?:apple\s+)?iphone\b",
        "actual_item": r"\bactual\s+item\s+(?:being\s+offered|you\s+receive)\b",
        "cell_phone_aspect": r"\btype\s*:?\s*iphone\b|\bmodel\s*:?\s*(?:apple\s+)?iphone\b",
        "factory_unlocked": r"\bfactory\s+unlocked\b|\bnetwork\s*:?\s*unlocked\b|\bcarrier\s+service\s+unlocked\b",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _functionality_signals(text: str) -> list[str]:
    patterns = {
        "powers_on": r"\bpowers?\s+on\b|\bphone\s+charges\s+and\s+powers?\s+on\b",
        "boots": r"\bboots?\b",
        "charges": r"\bcharges\b|\bphone\s+charges\b",
        "charge_port_functional": r"\bcharge\s+port\s+(?:is\s+)?(?:clean\s+and\s+)?fully\s+functional\b",
        "fully_functional": r"\bfully\s+functional\b",
        "tested_functional": r"\btested\s+and\s+(?:fully\s+)?functional\b",
        "phone_works": r"\bphone\s+works\b|\bdoes\s+still\s+work\b|\bdevice\s+works\b",
        "everything_else_functional": r"\beverything\s+else\s+remains\s+functional\b|\beverything\s+works\s+except\b",
        "face_id_works": r"\bface\s*id\s+(?:works|working|functions?\s+properly|works?\s+as\s+expected|is\s+ready\s+to\s+be\s+set\s+up)\b",
        "cameras_functional": r"\b(?:front\s+and\s+rear\s+)?cameras?\s+(?:are\s+)?(?:fully\s+)?functional\b|\bcameras?\s+works?\b|\bcameras?\s+are\s+in\s+good\s+shape\b",
        "touch_functional": r"\btouch\s+(?:works|working|functional)\b|\bdigitizer\s+\(?(?:touch\s+screen)?\)?\s+responds\s+to\s+touch\b",
        "display_functional": r"\b(?:lcd|oled|lcd/oled|display)\s+(?:has\s+no\s+issues|no\s+issues|fully\s+functional|works|working|functional)\b",
        "battery_health": r"\bbattery\s+health\s*:?\s*\d{1,3}\s*%",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _clean_activation_signals(text: str) -> list[str]:
    patterns = {
        "clean_imei": r"\bclean\s+imei\b",
        "clean_esn": r"\bclean\s+esn\b",
        "ready_to_activate": r"\bready\s+to\s+be\s+activated\b",
        "fmi_off": r"\bfmi\s+(?:off|removed)\b",
        "icloud_off": r"\bicloud\s+(?:off|removed|signed\s+out)\b",
        "no_icloud": r"\bno\s+icloud\b|\bno\s+icloud\s+lock(?:ed)?\b",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _repair_detail_signals(text: str) -> list[str]:
    patterns = {
        "cracked_screen": r"\bcracked\s+screen\b|\bfront\s+screen\s+is\s+cracked\b|\bscreen\s+(?:is\s+)?cracked\b",
        "cracked_back": r"\bcracked\s+back\b",
        "cracked_back_glass": r"\bback\s+glass\s+(?:is\s+)?cracked\b|\bcracked\s+back\s+glass\b",
        "bad_battery": r"\bbad\s+battery\b",
        "weak_battery": r"\bweak\s+battery\b",
        "service_battery": r"\bbattery\s+needs\s+to\s+be\s+serviced\b|\bservice\s+battery\b",
        "bad_lcd": r"\bbad\s+(?:screen|lcd)\b|\b(?:screen|lcd)\s+is\s+bad\b|\bno\s+(?:image|display)\b|\bblack\s+screen\b",
        "bad_oled": r"\bbad\s+oled\b|\boled\s+is\s+bad\b",
        "non_oem_screen": r"\bnon[-\s]?oem\s+screen\b|\bnon\s+apple\s+screen\b",
        "deep_scratches": r"\bdeep\s+scratches\b|\bscratches\s+are\s+semi\s+deep\b",
        "charging_port_issue": r"\b(?:charging|charge)(?:\s+port)?\s+(?:issue|problem|bad|broken|fault)\b|\b(?:does\s+not|doesn'?t|won'?t)\s+charge\b",
        "camera_fault": r"\b(?:front|rear|main|selfie)?\s*camera\s+(?:issue|problem|bad|broken|fault|not\s+working|doesn'?t\s+work)\b|\bbad\s+(?:(?:front|rear|main|selfie)\s+)?camera\b",
        "face_id_issue": r"\bface\s*id\s+(?:issue|problem|not\s+working|does\s+not\s+work|doesn'?t\s+work|broken|fail(?:ed|s)?)\b|\bbad\s+face\s*id\b",
        "digitizer_issue": r"\b(?:touch|digitizer)\s+(?:issue|problem|not\s+working|does\s+not\s+work|doesn'?t\s+work|broken|unresponsive)\b",
        "swollen_battery": r"\bswollen\s+battery\b",
        "no_ic_read": r"\bno\s+ic\s+read\b",
        "ic_issue": r"\bic\s+(?:issue|problem|bad)\b|\bbad\s+ic\b",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _component_reject_signals(text: str) -> list[str]:
    patterns = {
        "phone_not_included": r"\bphone\s+(?:is\s+)?not\s+included\b",
        "device_not_included": r"\bdevice\s+(?:is\s+)?not\s+included\b",
        "screen_only": r"\bscreen\s+only\b",
        "display_only": r"\bdisplay\s+only\b",
        "oled_only": r"\boled\s+only\b",
        "lcd_only": r"\blcd\s+only\b",
        "display_assembly": r"\bdisplay\s+assembly\b|\bscreen\s+display\s+assembly\b|\bscreen\s+assembly\b",
        "flex_parts_only": r"\bfor\s+flex\s+parts\s+only\b|\bflex\s+parts\s+only\b",
        "replacement_screen": r"\breplacement\s+screen\b|\breplacement\s+display\b|\bscreen\s+for\s+iphone\b",
        "housing_only": r"\bhousing\s+only\b",
        "box_only": r"\bbox\s+only\b|\bempty\s+box\b",
        "part_only": r"\bback\s+glass\s+part\s+only\b|\bcamera\s+only\b|(?<!bad\s)(?<!weak\s)\bbattery\s+only\b",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)]


def _normal_not_included_accessory_signals(text: str) -> list[str]:
    signals = []
    if NORMAL_NOT_INCLUDED_ACCESSORY_PATTERN.search(text):
        signals.append("normal_accessories_not_included")
    for name, pattern in {
        "sim_card_not_included": r"\b(?:not\s+included|no)\s*:?\s*(?:[-\s/]*sim\s+card|sim\s+card)\b",
        "charger_not_included": r"\b(?:not\s+included|no)\s*:?\s*(?:[-\s/]*charger|charger)\b",
        "headphones_not_included": r"\b(?:not\s+included|no)\s*:?\s*(?:[-\s/]*headphones?|headphones?)\b",
        "box_not_included": r"\b(?:not\s+included|no)\s*:?\s*(?:[-\s/]*(?:original\s+)?box|(?:original\s+)?box)\b|\bno\s+box\b",
    }.items():
        if re.search(pattern, text, re.IGNORECASE):
            signals.append(name)
    return signals


def _collect_storageish_values(value: Any, parent_key: str = "") -> list[str]:
    values: list[str] = []
    storage_key = bool(re.search(r"\b(?:storage|capacity|memory)\b", parent_key, re.IGNORECASE))
    if isinstance(value, dict):
        for key, nested in value.items():
            values.extend(_collect_storageish_values(nested, str(key)))
        return values
    if isinstance(value, list):
        for nested in value:
            values.extend(_collect_storageish_values(nested, parent_key))
        return values
    if value is None:
        return values
    text = str(value)
    if storage_key or _storage_from_text(text):
        values.append(text)
    return values


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _storage_from_text(text: str) -> str | None:
    match = STORAGE_PATTERN.search(text)
    if not match:
        return None
    if match.group(2):
        return "1TB"
    return f"{match.group(1)}GB"


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
    high_confidence_component = bool(HIGH_CONFIDENCE_SCREEN_COMPONENT_PATTERN.search(title))
    if (high_confidence_component and CLEAR_HANDSET_DAMAGE_PATTERN.search(title)
            and (STORAGE_PATTERN.search(title) or CARRIER_PATTERN.search(title))
            and not re.search(r"\b(?:oem|replacement|assembly|digitizer|screen\s+for\s+iphone)\b", title, re.I)):
        return []
    if not high_confidence_component and not PARTS_ONLY_PATTERN.search(title) and not SCREEN_PART_LISTING_PATTERN.search(title):
        return []
    if not DISPLAY_PART_PATTERN.search(title):
        return []
    if not high_confidence_component and (STORAGE_PATTERN.search(title) or CARRIER_PATTERN.search(title)):
        return []
    if not high_confidence_component and WHOLE_PHONE_PROOF_PATTERN.search(title):
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


STRUCTURAL_COMPONENT_PATTERN = re.compile(
    r"\b(?:logic\s+board|motherboard|camera\s+module|replacement\s+battery|"
    r"battery\s+replacement\s+part|charging[\s-]*(?:port\s+)?flex|camera\s+lens\s+assembly|"
    r"(?:back|rear)\s+housing|chassis|frame\s+assembly)\b",
    re.IGNORECASE,
)
OEM_COMPONENT_PATTERN = re.compile(
    r"\b(?:oem|genuine|original|replacement|compatible)\b.{0,35}\b"
    r"(?:screen|display|oled|lcd|digitizer|housing|chassis|logic\s+board|"
    r"motherboard|camera|battery|charging[\s-]*port|flex|lens)\b",
    re.IGNORECASE,
)
CLEAR_HANDSET_DAMAGE_PATTERN = re.compile(
    r"\b(?:(?:cracked|broken|damaged|bad|faulty)\s+(?:front\s+|back\s+)?"
    r"(?:screen|display|oled|lcd|back\s+glass|battery|camera|face\s*id)|"
    r"(?:screen|display|oled|lcd|battery|face\s*id)\s+(?:is\s+)?"
    r"(?:cracked|broken|damaged|bad|faulty)|"
    r"needs?\s+(?:a\s+)?(?:new|replacement)\s+(?:battery|screen|display)|"
    r"battery\s+needs?\s+(?:a\s+)?replacement|"
    r"cracked\s+back\s+glass)\b",
    re.IGNORECASE,
)


def _structural_component_suppress_flags(title: str) -> list[str]:
    match = STRUCTURAL_COMPONENT_PATTERN.search(title)
    if not match and not CLEAR_HANDSET_DAMAGE_PATTERN.search(title):
        match = OEM_COMPONENT_PATTERN.search(title)
    if not match:
        return []
    phrase = match.group(0).lower()
    if "housing" in phrase or "chassis" in phrase or "frame" in phrase:
        return ["housing_not_phone"]
    if "board" in phrase:
        return ["motherboard"]
    if "battery" in phrase:
        return ["battery_part_not_phone"]
    return ["replacement_part_not_phone"]


def _clear_handset_evidence(
    title: str, positive_flags: list[str], description_signals: dict[str, list[str]],
) -> bool:
    explicit_description_proof = [
        signal for signal in description_signals.get("whole_phone_evidence", [])
        if signal != "iphone_title_or_specs"
    ]
    if description_signals.get("included_device_signals") or explicit_description_proof:
        return True
    if re.search(r"\b(?:complete|whole|entire)\s+(?:iphone|phone|handset)\b|\b(?:phone|handset)\s+(?:is\s+)?(?:included|works|powers?\s+on)\b", title, re.I):
        return True
    if CLEAR_HANDSET_DAMAGE_PATTERN.search(title):
        return True
    if re.search(r"\biphone\b.*\bas[\s-]?is\b.*\bread\s+description\b", title, re.I):
        return True
    # A clearly described handset that fully works is stronger evidence than
    # a bare model number. Component titles are rejected before this step.
    if (IPHONE_TITLE_PATTERN.search(title) and STORAGE_PATTERN.search(title)
            and re.search(r"\b(?:fully\s+(?:working|works|functional)|everything\s+works|works\s+perfectly)\b", title, re.I)):
        return True
    if (PARTS_ONLY_PATTERN.search(title) and STORAGE_PATTERN.search(title)
            and CARRIER_PATTERN.search(title) and re.search(r"\bread\s+description\b", title, re.I)):
        return True
    specific = ACTUAL_REPAIR_ISSUE_FLAGS.intersection(positive_flags)
    return bool(specific and (STORAGE_PATTERN.search(title) or CARRIER_PATTERN.search(title)))


def _description_component_suppress_flags(title: str, description: str, description_signals: dict[str, list[str]] | None = None) -> list[str]:
    if not description:
        return []
    description_signals = description_signals or {}
    component_signals = set(description_signals.get("component_reject_signals") or [])
    if not component_signals:
        return []
    combined = f"{title} {description}"
    flags = ["screen_part_not_phone"] if DISPLAY_PART_PATTERN.search(combined) or component_signals.intersection(
        {"screen_only", "display_only", "oled_only", "lcd_only", "display_assembly", "replacement_screen", "flex_parts_only"}
    ) else ["replacement_part_not_phone"]
    if re.search(r"\b(?:oled|lcd)\b", combined, re.IGNORECASE) or component_signals.intersection({"oled_only", "lcd_only"}):
        flags.append("oled_lcd_part_not_phone")
    if re.search(r"\b(?:assembly|display|screen)\b", combined, re.IGNORECASE) or component_signals.intersection(
        {"display_assembly", "screen_only", "display_only", "replacement_screen"}
    ):
        flags.append("display_assembly_not_phone")
    if component_signals.intersection({"housing_only"}):
        flags.append("housing_not_phone")
    if component_signals.intersection({"box_only"}):
        flags.append("accessory_not_phone")
    if component_signals.intersection({"part_only"}):
        flags.append("battery_part_not_phone")
    return flags


def _high_resale_model_verification_eligibility(
    *,
    listing: dict[str, Any],
    model: str,
    storage_capacity: str | None,
    resale: dict[str, Any],
    profit_mid: float,
    parts_pricing_status: str,
    estimated_profit_available: bool,
    proof_flags: list[str],
) -> tuple[bool, list[str]]:
    if not estimated_profit_available or model == "unknown":
        return True, []
    resale_mid = float(resale.get("mid") or 0)
    if not _is_high_resale_or_new_model(model, resale_mid, profit_mid):
        return True, []
    if parts_pricing_status in VERIFIED_PART_STATUSES and (storage_capacity or len(proof_flags) >= 2):
        return True, []
    if parts_pricing_status in {"fallback", "estimated", "verified_screenshot_low_confidence", "manual_part_update"}:
        return False, ["High-resale model needs stronger verification"]
    if not storage_capacity and not _has_structured_model_confirmation(listing):
        return False, ["High-resale model needs stronger verification"]
    return True, []


def _storage_unknown_verification_eligibility(
    *,
    storage_capacity: str | None,
    resale: dict[str, Any],
    proof_flags: list[str],
    estimated_profit_available: bool,
) -> tuple[bool, list[str]]:
    if not estimated_profit_available or storage_capacity:
        return True, []
    if resale.get("source") not in {"model_range", "legacy_resale_value"}:
        return True, []
    if len(proof_flags) >= 2:
        return True, []
    return False, ["Storage unknown needs review"]


def _is_high_resale_or_new_model(model: str, resale_mid: float, profit_mid: float) -> bool:
    if re.match(r"\biphone\s+1[6-9]\b", model, re.IGNORECASE):
        return True
    return resale_mid >= 1000 or profit_mid >= 300


def _has_structured_model_confirmation(listing: dict[str, Any]) -> bool:
    raw_json = listing.get("raw_json") or {}
    if not isinstance(raw_json, dict):
        return False
    blob = _normalize(" ".join(str(value) for value in _collect_modelish_values(raw_json) if value))
    return bool(blob and IPHONE_TITLE_PATTERN.search(blob))


def _collect_modelish_values(value: Any, parent_key: str = "") -> list[str]:
    values: list[str] = []
    model_key = bool(re.search(r"(?:^|[_\s])(?:model|modelname|modelnumber|product)(?:$|[_\s])", parent_key, re.IGNORECASE))
    if isinstance(value, dict):
        name = str(value.get("name") or "")
        nested_value = value.get("value")
        if re.search(r"\bmodel\b", name, re.IGNORECASE) and nested_value is not None:
            values.append(str(nested_value))
        for key, nested in value.items():
            if str(key).lower() in {"title", "description", "shortdescription"}:
                continue
            values.extend(_collect_modelish_values(nested, str(key)))
        return values
    if isinstance(value, list):
        for nested in value:
            values.extend(_collect_modelish_values(nested, parent_key))
        return values
    if value is not None and model_key:
        values.append(str(value))
    return values


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
    source = _model_estimate_source(model, repair_values)
    if source:
        return repair_values[source]
    return repair_values.get("default", {})


def _model_estimate_source(model: str, repair_values: dict[str, Any]) -> str | None:
    if model in repair_values:
        return model
    generation = _iphone_generation(model)
    if generation is None:
        return None
    variant = _iphone_variant(model)
    candidates = [key for key in repair_values if key != "default" and _iphone_generation(key) is not None]
    candidates.sort(
        key=lambda key: (
            abs((_iphone_generation(key) or generation) - generation),
            0 if _iphone_variant(key) == variant else 1,
            0 if _iphone_variant(key) == "base" else 1,
        )
    )
    return candidates[0] if candidates else None


def _iphone_generation(model: str) -> int | None:
    match = re.search(r"\biphone\s+(\d{2})\b", str(model), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _iphone_variant(model: str) -> str:
    lowered = str(model).lower()
    for variant in ("pro max", "pro", "plus", "mini", "air"):
        if variant in lowered:
            return variant
    return "base"


def _resale_estimate(
    estimate: dict[str, Any],
    storage_capacity: str | None = None,
    *,
    model: str = "unknown",
    pricing_model: str | None = None,
    resale_research: dict[str, Any] | None = None,
) -> dict[str, Any]:
    research_model = pricing_model or model
    research_entry = _model_research_entry(resale_research or {}, model)
    used_nearest_model = research_model != model
    if not research_entry and research_model != model:
        research_entry = _model_research_entry(resale_research or {}, research_model)
    research_storage = research_entry.get("resale_by_storage") if isinstance(research_entry.get("resale_by_storage"), dict) else {}
    storage_warning = ""
    if research_storage and storage_capacity:
        storage_used = _storage_range_key(research_storage, storage_capacity)
        if storage_used:
            if storage_used != storage_capacity:
                storage_warning = f"No exact {storage_capacity} resale range; using closest lower {storage_used}"
            result = _resale_range_from_mapping(
                research_storage.get(storage_used) or {},
                source="estimated_nearest_model" if used_nearest_model else "storage_specific",
                market_source="resale_research",
                storage_used=storage_used,
                storage_warning=storage_warning,
            )
            if used_nearest_model:
                result["note"] = _join_note(result["note"], f"Estimated from {research_model}; exact {model} resale unavailable.")
            return result

    research_resale = research_entry.get("resale") if isinstance(research_entry.get("resale"), dict) else {}
    if research_resale:
        if research_storage and not storage_capacity:
            storage_warning = "Storage unknown - model-level resale used"
        elif research_storage and storage_capacity:
            storage_warning = f"No storage-specific resale range for {storage_capacity}; model-level resale used"
        result = _resale_range_from_mapping(
            research_resale,
            source="estimated_nearest_model" if used_nearest_model else "model_range",
            market_source="resale_research",
            storage_used=None,
            storage_warning=storage_warning,
        )
        if used_nearest_model:
            result["note"] = _join_note(result["note"], f"Estimated from {research_model}; exact {model} resale unavailable.")
        return result

    storage_ranges = estimate.get("resale_by_storage") if isinstance(estimate.get("resale_by_storage"), dict) else {}
    storage_warning = ""
    if storage_ranges and storage_capacity:
        storage_used = _storage_range_key(storage_ranges, storage_capacity)
        if storage_used:
            if storage_used != storage_capacity:
                storage_warning = f"No exact {storage_capacity} resale range; using closest lower {storage_used}"
            result = _resale_range_from_mapping(
                storage_ranges.get(storage_used) or {},
                source="estimated_nearest_model" if used_nearest_model else "storage_specific",
                market_source="repair_values",
                storage_used=storage_used,
                storage_warning=storage_warning,
            )
            if used_nearest_model:
                result["note"] = _join_note(result["note"], f"Estimated from {research_model}; exact {model} resale unavailable.")
            return result

    resale = estimate.get("resale") if isinstance(estimate.get("resale"), dict) else {}
    if storage_ranges and not storage_capacity and resale:
        storage_warning = "Storage unknown - model-level resale used"
    elif storage_ranges and storage_capacity and resale:
        storage_warning = f"No storage-specific resale range for {storage_capacity}; model-level resale used"
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
    if mid <= 0:
        source = "missing"
    elif used_nearest_model:
        source = "estimated_nearest_model"
    elif resale:
        source = "model_range"
    else:
        source = "legacy_resale_value"
    return {
        "low": low,
        "mid": mid,
        "high": high,
        "confidence": str(resale.get("confidence") or ""),
        "sample_size": int(resale.get("sample_size") or 0),
        "note": _join_note(
            str(resale.get("note") or ""),
            f"Estimated from {research_model}; exact {model} resale unavailable." if used_nearest_model and mid > 0 else "",
        ),
        "source": source,
        "market_source": "repair_values" if source in {"model_range", "estimated_nearest_model"} else "legacy_resale_value" if source == "legacy_resale_value" else "missing",
        "condition_used": "Good" if source != "missing" else "",
        "mint": _empty_range(),
        "storage_used": None,
        "storage_warning": storage_warning,
    }


def _empty_resale_estimate() -> dict[str, Any]:
    return {
        "low": 0.0,
        "mid": 0.0,
        "high": 0.0,
        "confidence": "",
        "sample_size": 0,
        "note": "",
        "source": "missing",
        "market_source": "missing",
        "condition_used": "",
        "mint": _empty_range(),
        "storage_used": None,
        "storage_warning": "",
    }


def _model_research_entry(resale_research: dict[str, Any], model: str) -> dict[str, Any]:
    if not isinstance(resale_research, dict):
        return {}
    if model in resale_research and isinstance(resale_research[model], dict):
        return resale_research[model]
    models = resale_research.get("models")
    if isinstance(models, dict) and isinstance(models.get(model), dict):
        return models[model]
    return {}


def _storage_range_key(storage_ranges: dict[str, Any], storage_capacity: str) -> str | None:
    normalized = {_normalize_storage_key(key): key for key in storage_ranges}
    if storage_capacity in normalized:
        return normalized[storage_capacity]
    if storage_capacity not in STORAGE_ORDER:
        return None
    target_index = STORAGE_ORDER.index(storage_capacity)
    for capacity in reversed(STORAGE_ORDER[:target_index]):
        if capacity in normalized:
            return normalized[capacity]
    return None


def _normalize_storage_key(value: Any) -> str:
    return _storage_from_text(str(value)) or str(value).replace(" ", "").upper()


def _resale_range_from_mapping(
    resale: dict[str, Any],
    *,
    source: str,
    market_source: str,
    storage_used: str | None,
    storage_warning: str,
) -> dict[str, Any]:
    good = resale.get("good") if isinstance(resale.get("good"), dict) else resale
    mint = resale.get("mint") if isinstance(resale.get("mint"), dict) else {}
    mid = _float_or_none(good.get("mid"))
    fallback = _float_or_none(good.get("resale_value"))
    if mid is None:
        mid = fallback or 0.0
    low = _float_or_none(good.get("low"))
    high = _float_or_none(good.get("high"))
    if low is None:
        low = mid
    if high is None:
        high = mid
    mint_range = _range_from_mapping(mint)
    return {
        "low": low,
        "mid": mid,
        "high": high,
        "confidence": str(good.get("confidence") or resale.get("confidence") or ""),
        "sample_size": int(good.get("sample_size") or resale.get("sample_size") or 0),
        "note": str(good.get("note") or good.get("notes") or resale.get("note") or resale.get("notes") or ""),
        "source": source if mid > 0 else "missing",
        "market_source": market_source if mid > 0 else "missing",
        "condition_used": "Good" if mid > 0 else "",
        "mint": mint_range,
        "storage_used": storage_used,
        "storage_warning": storage_warning,
    }


def _range_from_mapping(value: dict[str, Any]) -> dict[str, float]:
    if not isinstance(value, dict):
        return _empty_range()
    mid = _float_or_none(value.get("mid"))
    fallback = _float_or_none(value.get("resale_value"))
    if mid is None:
        mid = fallback or 0.0
    low = _float_or_none(value.get("low"))
    high = _float_or_none(value.get("high"))
    if low is None:
        low = mid
    if high is None:
        high = mid
    return {"low": low, "mid": mid, "high": high}


def _empty_range() -> dict[str, float]:
    return {"low": 0.0, "mid": 0.0, "high": 0.0}


def _profit_range(
    resale: dict[str, Any],
    *,
    total_cost: float,
    estimated_parts_cost: float,
    risk_buffer: float,
    available: bool,
    exit_cost_model: dict[str, Any] | None = None,
) -> tuple[float, float, float]:
    if not available:
        return 0.0, 0.0, 0.0
    cost_basis = total_cost + estimated_parts_cost + risk_buffer

    def net_profit(sale_price: float) -> float:
        exit_costs = _estimated_exit_costs(sale_price, exit_cost_model)
        return sale_price - cost_basis - exit_costs["total"]

    return (
        net_profit(float(resale["low"])),
        net_profit(float(resale["mid"])),
        net_profit(float(resale["high"])),
    )


def _exit_cost_model(resale_research: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(resale_research, dict):
        return {}
    model = resale_research.get("__exit_cost_model__")
    if not isinstance(model, dict) or not bool(model.get("enabled")):
        return {}
    return model


def _estimated_exit_costs(sale_price: float, model: dict[str, Any] | None) -> dict[str, float]:
    if sale_price <= 0 or not model:
        return {"selling_fees": 0.0, "outbound_shipping": 0.0, "total": 0.0}

    seller_fee_rate = max(0.0, float(model.get("seller_fee_rate") or 0.0))
    buyer_fee_rate = max(0.0, float(model.get("buyer_fee_rate") or 0.0))
    processing_rate = max(0.0, float(model.get("payment_processing_rate") or 0.0))
    processing_fixed = max(0.0, float(model.get("payment_processing_fixed") or 0.0))
    processing_base = sale_price
    if bool(model.get("payment_processing_base_includes_buyer_fee")):
        processing_base *= 1.0 + buyer_fee_rate

    selling_fees = (sale_price * seller_fee_rate) + (processing_base * processing_rate) + processing_fixed
    outbound_shipping = _tiered_outbound_shipping(sale_price, model.get("outbound_shipping_tiers"))
    return {
        "selling_fees": selling_fees,
        "outbound_shipping": outbound_shipping,
        "total": selling_fees + outbound_shipping,
    }


def _tiered_outbound_shipping(sale_price: float, tiers: Any) -> float:
    if not isinstance(tiers, list):
        return 0.0
    fallback = 0.0
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        cost = max(0.0, float(tier.get("cost") or 0.0))
        max_sale_price = tier.get("max_sale_price")
        if max_sale_price is None:
            fallback = cost
            continue
        try:
            if sale_price <= float(max_sale_price):
                return cost
        except (TypeError, ValueError):
            continue
    return fallback


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


def _estimate_parts_cost(
    estimate: dict[str, Any],
    positive_flags: list[str],
    *,
    forced_issue_type: str | None = None,
    forced_part_cost: float | None = None,
) -> tuple[float, bool]:
    if forced_part_cost is not None:
        return round(float(forced_part_cost), 2), float(forced_part_cost) > 0
    issue_costs = []
    missing_required_cost = False
    forced_flag = _issue_flag_from_override(forced_issue_type)
    active_flags = [forced_flag] if forced_flag else list(positive_flags)
    for flag, cost_keys in ISSUE_COST_KEYS.items():
        if flag not in active_flags:
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


def _estimate_parts_cost_from_nearest_models(
    model: str,
    repair_values: dict[str, Any],
    positive_flags: list[str],
    *,
    forced_issue_type: str | None = None,
) -> tuple[float, list[str]]:
    """Use conservative nearby-model repair prices and disclose every source."""
    generation = _iphone_generation(model)
    if generation is None:
        return 0.0, []
    forced_flag = _issue_flag_from_override(forced_issue_type)
    active_flags = [forced_flag] if forced_flag else list(positive_flags)
    total = 0.0
    sources: list[str] = []
    for flag, cost_keys in ISSUE_COST_KEYS.items():
        if flag not in active_flags:
            continue
        candidates: list[tuple[int, int, float, str]] = []
        for candidate_model, candidate_estimate in repair_values.items():
            candidate_generation = _iphone_generation(candidate_model)
            if candidate_generation is None or abs(candidate_generation - generation) > 2:
                continue
            cost = _estimate_cost(candidate_estimate, cost_keys)
            if cost is None or cost <= 0:
                continue
            candidates.append(
                (
                    abs(candidate_generation - generation),
                    0 if _iphone_variant(candidate_model) == _iphone_variant(model) else 1,
                    float(cost),
                    candidate_model,
                )
            )
        if not candidates:
            return 0.0, []
        candidates.sort(key=lambda value: (value[0], value[1], -value[2]))
        nearest_distance = candidates[0][0]
        nearest = [candidate for candidate in candidates if candidate[0] == nearest_distance][:3]
        conservative = max(candidate[2] for candidate in nearest)
        total += conservative
        sources.append(f"{nearest[0][3]} {flag}")
    return round(total, 2), sources


def _join_note(existing: str, addition: str) -> str:
    return " ".join(part.strip() for part in (existing, addition) if part and part.strip())


def _issue_flag_from_override(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    mapping = {
        "screen_budget": "cracked_screen",
        "screen_safe": "cracked_screen",
        "screen_premium": "cracked_screen",
        "cracked_screen": "cracked_screen",
        "screen_display_issue": "screen_display_issue",
        "bad_oled": "bad_oled",
        "battery": "bad_battery",
        "bad_battery": "bad_battery",
        "back_glass": "back_glass_cracked",
        "back_glass_cracked": "back_glass_cracked",
        "camera_lens": "camera_lens_cracked",
        "camera_lens_cracked": "camera_lens_cracked",
        "charging_port": "charging_port_issue",
        "charging_port_issue": "charging_port_issue",
    }
    return mapping.get(normalized)


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
    storage_resale_warning: str = "",
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
    if storage_resale_warning:
        reasons.append(storage_resale_warning)
    classification_flags = set(classification.get("flags") or [])
    description_supports_review = bool(
        classification_flags.intersection(
            {
                "description_functionality_evidence",
                "normal_accessory_exclusions",
            }
        )
    )
    pricing_blocks_best_pick = (
        not parts_cost_available
        or not estimated_profit_available
        or parts_pricing_status in UNVERIFIED_PART_STATUSES
        or any(
            reason in (alert_ineligible_reasons or [])
            for reason in (
                "Expected profit below threshold",
                "Only upside case works",
                "Low-confidence pricing needs stronger profit",
                "Profit depends on mint resale",
            )
        )
    )
    if (
        description_supports_review
        and classification["whole_phone_confidence_passed"]
        and classification["has_repair_issue"]
        and not classification["suppress_flags"]
    ):
        reasons.append("Description supports whole-phone review")
        if pricing_blocks_best_pick:
            reasons.append("Pricing confidence prevents Best Pick")
            reasons.append("Reviewable despite parts/pricing gap")
    risk_reason_labels = {
        "ic_issue": "IC issue risk",
        "no_ic_read": "No IC READ",
        "ic_read": "IC read mentioned",
        "not_original_owner": "Not original owner",
        "unknown_icloud": "Cannot verify iCloud",
        "face_id_unknown": "Face ID unknown",
        "touch_not_working": "Touch not working",
        "display_not_original": "Display not original",
        "parts_swapped": "Parts swapped",
    }
    reasons.extend(label for flag, label in risk_reason_labels.items() if flag in risk_flags)
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
        reasons.append("Only upside case works")
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


def _risk_flags_for_score(
    risk_flags: list[str],
    *,
    proof_flags: list[str],
    hard_flags: list[str],
    classification: dict[str, Any],
    estimated_profit_available: bool,
    profit_mid: float,
    min_profit: float,
) -> list[str]:
    if not {"for_parts", "as_is"}.intersection(risk_flags):
        return risk_flags
    if _for_parts_as_is_is_context_only(
        proof_flags=proof_flags,
        hard_flags=hard_flags,
        classification=classification,
        estimated_profit_available=estimated_profit_available,
        profit_mid=profit_mid,
        min_profit=min_profit,
    ):
        return [flag for flag in risk_flags if flag not in {"for_parts", "as_is"}]
    return risk_flags


def _for_parts_as_is_is_context_only(
    *,
    proof_flags: list[str],
    hard_flags: list[str],
    classification: dict[str, Any],
    estimated_profit_available: bool,
    profit_mid: float,
    min_profit: float,
) -> bool:
    dangerous_flags = {
        "no_power",
        "does_not_turn_on",
        "icloud_locked",
        "activation_locked",
        "mdm_locked",
        "blacklisted",
        "bad_esn",
        "no_service",
        "baseband",
        "water_damage",
        "liquid_damage",
        "logic_board",
        "motherboard",
        "board_damage",
        "motherboard_issue",
        "logic_issue",
    }
    if dangerous_flags.intersection(hard_flags):
        return False
    if not classification.get("whole_phone_confidence_passed") or not classification.get("has_specific_repair_issue"):
        return False
    return bool(proof_flags) or (estimated_profit_available and profit_mid >= min_profit * 1.5)


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
