from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

import sqlalchemy as sa

from backend.config import load_settings
from backend.db import create_sqlalchemy_engine, get_database_url


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _notification_status(row: dict[str, Any], *, global_configured: bool) -> dict[str, Any]:
    alerts_enabled = bool(row.get("alerts_enabled"))
    discord_enabled = bool(row.get("discord_enabled"))
    per_user = bool(row.get("discord_webhook"))
    global_opt_in = bool(row.get("use_global_discord_webhook"))
    if not alerts_enabled:
        reason = "notifications_disabled"
    elif not discord_enabled:
        reason = "discord_disabled"
    elif per_user or (global_opt_in and global_configured):
        reason = None
    elif global_configured and not global_opt_in:
        reason = "global_fallback_not_authorized"
    else:
        reason = "webhook_missing"
    return {
        "user_id": row.get("user_id"),
        "alerts_enabled": alerts_enabled,
        "discord_enabled": discord_enabled,
        "notify_best_finds": bool(row.get("notify_best_finds")),
        "notify_priority_review": bool(row.get("notify_priority_review")),
        "per_user_webhook_configured": per_user,
        "use_global_discord_webhook": global_opt_in,
        "global_discord_webhook_configured": global_configured,
        "webhook_source": "per_user" if per_user else "global_opt_in" if global_opt_in and global_configured else None,
        "notification_ready": reason is None,
        "notification_block_reason": reason,
    }


def build_report(first_cycle: int, last_cycle: int) -> dict[str, Any]:
    settings = load_settings()
    engine = create_sqlalchemy_engine(get_database_url(settings))
    with engine.connect() as connection:
        cycle_rows = [
            dict(row._mapping)
            for row in connection.execute(
                sa.text(
                    "SELECT * FROM scan_cycles WHERE id BETWEEN :first AND :last ORDER BY id"
                ),
                {"first": first_cycle, "last": last_cycle},
            )
        ]
        notification_rows = [
            dict(row._mapping)
            for row in connection.execute(sa.text("SELECT * FROM user_notification_settings ORDER BY user_id"))
        ]
        user_thresholds = {
            int(row.user_id): float(row.min_profit_to_alert)
            for row in connection.execute(sa.text("SELECT user_id, min_profit_to_alert FROM user_settings"))
        }
        alerted = {
            (int(row.user_id), str(row.item_id))
            for row in connection.execute(
                sa.text(
                    """
                    SELECT uis.user_id, mi.marketplace_item_id AS item_id
                    FROM user_item_states uis
                    JOIN marketplace_items mi ON mi.id = uis.marketplace_item_id
                    WHERE uis.alerted_at IS NOT NULL
                    """
                )
            )
        }
        trace_rows = [
            dict(row._mapping)
            for row in connection.execute(
                sa.text(
                    """
                    SELECT ldt.user_id, ldt.scan_cycle_id, ldt.trace_json,
                           mi.marketplace_item_id AS item_id, mi.title
                    FROM listing_decision_traces ldt
                    JOIN marketplace_items mi ON mi.id = ldt.marketplace_item_id
                    WHERE ldt.scan_cycle_id BETWEEN :first AND :last
                    ORDER BY ldt.scan_cycle_id, ldt.id
                    """
                ),
                {"first": first_cycle, "last": last_cycle},
            )
        ]
    engine.dispose()

    configs = {
        int(row["user_id"]): _notification_status(
            row,
            global_configured=bool(settings.discord_webhook_url),
        )
        for row in notification_rows
    }
    by_cycle: dict[int, dict[str, Any]] = {}
    examples: list[dict[str, Any]] = []
    traces_by_cycle: dict[int, list[dict[str, Any]]] = {}
    for row in trace_rows:
        traces_by_cycle.setdefault(int(row["scan_cycle_id"]), []).append(row)

    for cycle in cycle_rows:
        counts = Counter()
        for row in traces_by_cycle.get(int(cycle["id"]), []):
            trace = _json(row.get("trace_json"), {})
            verdict = trace.get("verdict") or {}
            reasons = trace.get("reasons") or {}
            pricing = trace.get("pricing") or {}
            blocks = set(reasons.get("blocking_rules") or [])
            normalized_bucket = verdict.get("normalized_bucket") or verdict.get("bucket")
            current_status = verdict.get("current_app_status") or verdict.get("app_status")
            scoring_eligible = normalized_bucket == "gem" or bool(verdict.get("alert_eligible")) and not blocks
            if normalized_bucket == "gem":
                counts["best_finds"] += 1
            if normalized_bucket == "good" and current_status not in {"candidate", "alerted"}:
                counts["priority_review"] += 1
            if bool(verdict.get("alert_eligible")):
                counts["otherwise_alert_eligible"] += 1
            if "score_below_threshold" in blocks:
                counts["blocked_score"] += 1
            min_profit = user_thresholds.get(int(row["user_id"]), 75.0)
            if "estimated_profit_unavailable" in blocks or float(pricing.get("profit_mid") or 0) < min_profit:
                counts["blocked_profit"] += 1
            if blocks.intersection({"stale_item", "not_fresh_for_alert", "item_age_above_alert_window"}):
                counts["blocked_freshness"] += 1
            if blocks.intersection({"whole_phone_confidence_failed", "hard_reject_flags", "repair_issue_missing"}):
                counts["blocked_whole_phone_or_damage"] += 1
            was_alerted = (int(row["user_id"]), str(row["item_id"])) in alerted
            if was_alerted:
                counts["already_alerted"] += 1
            config = configs.get(int(row["user_id"]), {})
            if scoring_eligible and not config.get("notification_ready"):
                counts["scoring_eligible_but_configuration_blocked"] += 1
                if len(examples) < 20:
                    examples.append(
                        {
                            "item_id": row["item_id"],
                            "title": row["title"],
                            "bucket": normalized_bucket,
                            "score": verdict.get("score"),
                            "estimated_profit": pricing.get("profit_mid"),
                            "alert_eligible": bool(verdict.get("alert_eligible")),
                            "previously_alerted": was_alerted,
                            "why_not_attempted": config.get("notification_block_reason"),
                        }
                    )
        by_cycle[int(cycle["id"])] = {
            "listings_found": int(cycle.get("items_found") or 0),
            "listings_scored": int(cycle.get("items_scored") or 0),
            "best_finds": counts["best_finds"],
            "priority_review": counts["priority_review"],
            "otherwise_alert_eligible": counts["otherwise_alert_eligible"],
            "blocked_score": counts["blocked_score"],
            "blocked_profit": counts["blocked_profit"],
            "blocked_freshness": counts["blocked_freshness"],
            "source_duplicates": int(cycle.get("duplicate_items") or 0),
            "already_alerted": counts["already_alerted"],
            "blocked_whole_phone_or_damage": counts["blocked_whole_phone_or_damage"],
            "scoring_eligible_but_configuration_blocked": counts["scoring_eligible_but_configuration_blocked"],
            "alerts_attempted": int(cycle.get("alerts_attempted") or 0),
            "alerts_sent": int(cycle.get("alerts_sent") or 0),
            "alerts_failed": int(cycle.get("alerts_failed") or 0),
        }

    totals = Counter()
    for counts in by_cycle.values():
        totals.update(counts)
    return {
        "cycle_range": [first_cycle, last_cycle],
        "stored_data_only": True,
        "configuration": list(configs.values()),
        "cycles": by_cycle,
        "totals": dict(totals),
        "scoring_eligible_examples_not_attempted": examples,
        "definitions": {
            "source_duplicates": "Listings already present in marketplace storage; they are still rescored and are not notification dedupe by themselves.",
            "blocked_profit": "Profit was unavailable or stored profit_mid was below the user's configured threshold; categories are non-exclusive.",
            "priority_review": "Stored normalized good bucket excluding candidate/alerted app statuses.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Report notification eligibility using stored cycles and traces only.")
    parser.add_argument("--first-cycle", type=int, required=True)
    parser.add_argument("--last-cycle", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(build_report(args.first_cycle, args.last_cycle), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
