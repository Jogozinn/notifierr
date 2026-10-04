"""Small deterministic identity for a scored decision; no source payload is persisted."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


@lru_cache(maxsize=1)
def _scorer_hash() -> str:
    return hashlib.sha256(Path(__file__).with_name("scorer.py").read_bytes()).hexdigest()[:24]


def decision_identity(
    *, scoring_rules: dict[str, Any], repair_values: dict[str, Any],
    resale_research: dict[str, Any], effective_settings: Any,
) -> dict[str, str]:
    config = {
        "scoring_rules": scoring_rules,
        "min_score_to_alert": effective_settings.min_score_to_alert,
        "min_profit_to_alert": effective_settings.min_profit_to_alert,
        "risky_score_range": [effective_settings.risky_score_min, effective_settings.risky_score_max],
    }
    return {
        "scorer_hash": _scorer_hash(),
        "rules_hash": _digest(config),
        "repair_hash": _digest(repair_values),
        "resale_hash": _digest(resale_research),
    }
