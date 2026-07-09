from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import main


STATUS_RANK = {
    "rejected": 0,
    "risky": 1,
    "candidate": 2,
    "alerted": 2,
}


@dataclass
class MaintenanceRescoreRequest:
    user_id: int
    item_ids: list[str] = field(default_factory=list)
    source_cycle_id: int | None = None
    limit: int = 100
    active_only: bool = False
    non_stale_only: bool = False
    not_ignored_only: bool = False
    include_candidates: bool = False
    include_risky: bool = False
    include_needs_data: bool = False
    include_rejected: bool = False
    write: bool = False
    write_traces: bool = False
    reason: str = "maintenance_rescore"
    report_dir: Path | str = Path("audit")


def run_maintenance_rescore(request: MaintenanceRescoreRequest) -> dict[str, Any]:
    user = main.storage.get_user(int(request.user_id), include_password_hash=True)
    if not user:
        raise ValueError(f"User {request.user_id} was not found")
    resolved = main._resolve_effective_user_settings(user)
    if not resolved.user:
        raise ValueError(f"User {request.user_id} is not available for rescore")

    user_id = int(resolved.user["id"])
    requested_ids = _normalized_item_ids(request.item_ids)
    source_item_ids = main._source_cycle_replay_item_ids(request.source_cycle_id)
    raw_items = main.storage.list_user_items(
        user_id,
        limit=500,
        include_ignored=True,
        include_stale=True,
        **resolved.freshness_kwargs(),
    )
    selected, skipped = _select_items(raw_items, request, requested_ids=requested_ids, source_item_ids=source_item_ids)
    cycle_id = None
    if request.write:
        cycle_id = main.storage.create_scan_cycle(
            mode="maintenance_rescore",
            user_id=user_id,
            users_considered=1,
            users_scanned=1,
            keywords_searched=resolved.keywords,
            sources_checked=["stored_marketplace_items", "user_item_states"],
            status="started",
            skip_reason=str(request.reason or "maintenance_rescore")[:500],
        )

    pricing_context = main._build_user_pricing_context(user_id)
    changed_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    written = 0
    counters: Counter[str] = Counter()

    try:
        for stored_item in selected:
            row = _rescore_item(
                stored_item,
                user_id=user_id,
                resolved=resolved,
                pricing_context=pricing_context,
                cycle_id=cycle_id,
            )
            all_rows.append(row)
            _count_row(counters, row)
            if row["changed"]:
                changed_rows.append(row)
            if request.write:
                main.storage.upsert_user_item_state(user_id, row["rescored_item"])
                written += 1
                if request.write_traces and cycle_id:
                    main.storage.record_listing_decision_trace(
                        user_id=user_id,
                        item_id=str(stored_item.get("item_id") or ""),
                        scan_cycle_id=cycle_id,
                        trace=row["trace"],
                    )

        summary = _summary(
            request=request,
            selected=selected,
            all_rows=all_rows,
            changed_rows=changed_rows,
            skipped=skipped,
            written=written,
            counters=counters,
            cycle_id=cycle_id,
        )
        if cycle_id:
            main.storage.finish_scan_cycle(
                cycle_id,
                status="completed",
                skip_reason=str(request.reason or "maintenance_rescore")[:500],
                users_considered=1,
                users_scanned=1,
                keywords_searched=resolved.keywords,
                sources_checked=["stored_marketplace_items", "user_item_states"],
                items_found=len(selected),
                items_scored=len(all_rows),
                alerts_attempted=0,
                alerts_sent=0,
                alerts_failed=0,
                final_bucket_counts=dict(counters),
                alert_block_reason_counts={},
                missing_data_reason_counts={},
                risk_flag_counts={},
            )
    except Exception as exc:
        if cycle_id:
            main.storage.finish_scan_cycle(
                cycle_id,
                status="failed",
                skip_reason=str(request.reason or "maintenance_rescore")[:500],
                error_message=f"{type(exc).__name__}: {exc}",
                users_considered=1,
                users_scanned=1,
                sources_checked=["stored_marketplace_items", "user_item_states"],
                alerts_attempted=0,
                alerts_sent=0,
                alerts_failed=0,
            )
        raise

    report_path = _write_report(summary, request.report_dir)
    return {**summary, "report_path": str(report_path)}


def _rescore_item(
    stored_item: dict[str, Any],
    *,
    user_id: int,
    resolved: Any,
    pricing_context: Any,
    cycle_id: int | None,
) -> dict[str, Any]:
    scoring_input = main._raw_listing_for_replay(stored_item)
    result, item_overrides = main._score_listing_for_user(
        scoring_input,
        resolved,
        pricing_context=pricing_context,
    )
    replay_item = {**stored_item, **scoring_input, **result.as_item_fields()}
    replay_item.update(
        main._repair_snapshot_for_item(
            replay_item,
            user_id=user_id,
            pricing_context=pricing_context,
            correction=item_overrides["correction"],
        )
    )
    replay_item = main._apply_availability_and_auction_policy(replay_item)
    replay_item = main._decorate_item_for_user(replay_item, user_id=user_id, pricing_context=pricing_context)
    alert_block_reasons = main._alert_block_reasons(replay_item, result, resolved)
    current_status = str(stored_item.get("status") or "")
    trace = main._build_decision_trace(
        replay_item,
        result,
        resolved,
        scan_cycle_id=cycle_id,
        alert_block_reasons=alert_block_reasons,
        current_app_status=current_status,
        current_app_bucket=current_status,
    )
    trace = main._attach_rescore_comparison(
        trace,
        persisted_item=stored_item,
        rescored_item=replay_item,
        rescore_from_raw=True,
    )
    comparison = trace.get("comparison") or {}
    changed = bool(
        comparison.get("changed_status")
        or comparison.get("changed_score")
        or comparison.get("changed_alert_eligibility")
        or comparison.get("changed_reasons")
    )
    return {
        "item_id": str(stored_item.get("item_id") or ""),
        "title": str(stored_item.get("title") or ""),
        "persisted_item": stored_item,
        "rescored_item": replay_item,
        "trace": trace,
        "comparison": comparison,
        "changed": changed,
        "sample": _sample_row(stored_item, replay_item, trace),
    }


def _select_items(
    raw_items: list[dict[str, Any]],
    request: MaintenanceRescoreRequest,
    *,
    requested_ids: set[str],
    source_item_ids: set[str],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    skipped: Counter[str] = Counter()
    filtered = list(raw_items)
    available_ids = {str(item.get("item_id") or "") for item in filtered}
    for item_id in requested_ids - available_ids:
        skipped["item_not_found"] += 1
    if source_item_ids:
        filtered = [item for item in filtered if str(item.get("item_id") or "") in source_item_ids]
    if requested_ids:
        filtered = [item for item in filtered if str(item.get("item_id") or "") in requested_ids]
    before = len(filtered)
    if request.active_only:
        filtered = [
            item
            for item in filtered
            if not item.get("stale")
            and item.get("user_status") not in {"ignored", "rejected"}
            and item.get("availability_status") not in {"sold", "ended", "unavailable"}
        ]
        skipped["inactive_filtered"] += before - len(filtered)
        before = len(filtered)
    if request.non_stale_only:
        filtered = [item for item in filtered if not item.get("stale")]
        skipped["stale_filtered"] += before - len(filtered)
        before = len(filtered)
    if request.not_ignored_only:
        filtered = [item for item in filtered if item.get("user_status") != "ignored"]
        skipped["ignored_filtered"] += before - len(filtered)
        before = len(filtered)
    if _has_include_filter(request):
        filtered = [item for item in filtered if _included_by_status_filter(item, request)]
        skipped["include_filter_excluded"] += before - len(filtered)
    limit = max(1, min(int(request.limit or 100), 500))
    if len(filtered) > limit:
        skipped["limit_excluded"] += len(filtered) - limit
    return filtered[:limit], skipped


def _included_by_status_filter(item: dict[str, Any], request: MaintenanceRescoreRequest) -> bool:
    status = str(item.get("status") or "")
    if request.include_candidates and status == "candidate":
        return True
    if request.include_risky and status == "risky":
        return True
    if request.include_rejected and (status == "rejected" or item.get("user_status") == "rejected"):
        return True
    if request.include_needs_data and main._is_needs_data_item(item):
        return True
    return False


def _has_include_filter(request: MaintenanceRescoreRequest) -> bool:
    return any(
        (
            request.include_candidates,
            request.include_risky,
            request.include_needs_data,
            request.include_rejected,
        )
    )


def _count_row(counters: Counter[str], row: dict[str, Any]) -> None:
    comparison = row["comparison"]
    persisted = row["persisted_item"]
    rescored = row["rescored_item"]
    persisted_status = str(comparison.get("persisted_status") or "")
    rescored_status = str(comparison.get("rescored_status") or "")
    if comparison.get("changed_status"):
        counters["status_changes"] += 1
    if comparison.get("changed_score"):
        counters["score_changes"] += 1
    if comparison.get("changed_alert_eligibility"):
        counters["alert_eligibility_changes"] += 1
    if persisted.get("alert_eligible") is True and rescored.get("alert_eligible") is False:
        counters["alert_eligible_true_to_false"] += 1
    if persisted.get("alert_eligible") is False and rescored.get("alert_eligible") is True:
        counters["alert_eligible_false_to_true"] += 1
    if persisted_status == "candidate" and rescored_status != "candidate":
        counters["candidates_downgraded"] += 1
    if persisted_status == "risky" and _status_rank(rescored_status) > _status_rank(persisted_status):
        counters["risky_upgraded"] += 1
    if persisted_status == "risky" and _status_rank(rescored_status) < _status_rank(persisted_status):
        counters["risky_downgraded"] += 1
    if main._is_needs_data_item(persisted) != main._is_needs_data_item(rescored):
        counters["needs_data_changed"] += 1
    if (persisted_status == "rejected") != (rescored_status == "rejected"):
        counters["rejected_changed"] += 1
    if not row["changed"]:
        counters["unchanged"] += 1


def _summary(
    *,
    request: MaintenanceRescoreRequest,
    selected: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    changed_rows: list[dict[str, Any]],
    skipped: Counter[str],
    written: int,
    counters: Counter[str],
    cycle_id: int | None,
) -> dict[str, Any]:
    return {
        "mode": "maintenance_rescore",
        "dry_run": not request.write,
        "write": bool(request.write),
        "write_traces": bool(request.write_traces and request.write),
        "reason": request.reason,
        "scan_cycle_id": cycle_id,
        "total_selected": len(selected),
        "total_rescored": len(all_rows),
        "written_count": written,
        "status_changes": counters["status_changes"],
        "score_changes": counters["score_changes"],
        "alert_eligibility_changes": counters["alert_eligibility_changes"],
        "candidates_downgraded": counters["candidates_downgraded"],
        "risky_upgraded": counters["risky_upgraded"],
        "risky_downgraded": counters["risky_downgraded"],
        "needs_data_changed": counters["needs_data_changed"],
        "rejected_changed": counters["rejected_changed"],
        "alert_eligible_true_to_false": counters["alert_eligible_true_to_false"],
        "alert_eligible_false_to_true": counters["alert_eligible_false_to_true"],
        "unchanged_count": counters["unchanged"],
        "skipped_count": sum(skipped.values()),
        "skipped_reasons": dict(skipped),
        "sample_changed_rows": [row["sample"] for row in changed_rows[:20]],
    }


def _sample_row(stored_item: dict[str, Any], replay_item: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    comparison = trace.get("comparison") or {}
    verdict = trace.get("verdict") or {}
    return {
        "item_id": str(stored_item.get("item_id") or ""),
        "title": str(stored_item.get("title") or ""),
        "persisted_status": comparison.get("persisted_status"),
        "rescored_status": comparison.get("rescored_status"),
        "persisted_score": comparison.get("persisted_score"),
        "rescored_score": comparison.get("rescored_score"),
        "persisted_alert_eligible": comparison.get("persisted_alert_eligible"),
        "rescored_alert_eligible": comparison.get("rescored_alert_eligible"),
        "persisted_manual_review_reason": comparison.get("persisted_manual_review_reason"),
        "rescored_manual_review_reason": comparison.get("rescored_manual_review_reason"),
        "changed_reasons": comparison.get("changed_reasons") or [],
        "confidence": verdict.get("confidence") or replay_item.get("whole_phone_score"),
    }


def _write_report(summary: dict[str, Any], report_dir: Path | str) -> Path:
    path = Path(report_dir)
    path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = path / f"maintenance_rescore_{timestamp}.json"
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return report_path


def _normalized_item_ids(item_ids: list[str]) -> set[str]:
    normalized: set[str] = set()
    for value in item_ids:
        for item_id in str(value or "").split(","):
            item_id = item_id.strip()
            if item_id:
                normalized.add(item_id)
    return normalized


def _status_rank(status: str) -> int:
    return STATUS_RANK.get(str(status or ""), 1)
