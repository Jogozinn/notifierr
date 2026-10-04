from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa


TABLES = (
    "scan_cycles",
    "notification_attempts",
    "push_delivery_attempts",
    "user_item_feedback",
    "user_item_outcomes",
    "listing_decision_traces",
    "shared_scan_searches",
    "shared_scan_results",
)


def _load_state(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): max(0, int(value)) for key, value in payload.get("cursor", {}).items()}


def _rows_since(connection: sa.Connection, table: str, after_id: int, limit: int) -> list[dict[str, Any]]:
    statement = sa.text(f'SELECT * FROM "{table}" WHERE id > :after_id ORDER BY id ASC LIMIT :limit')
    return [dict(row) for row in connection.execute(statement, {"after_id": after_id, "limit": limit}).mappings()]


def _render_report(rows: dict[str, list[dict[str, Any]]], started_at: datetime) -> str:
    scans = rows.get("scan_cycles", [])
    notifications = rows.get("notification_attempts", [])
    push = rows.get("push_delivery_attempts", [])
    feedback = rows.get("user_item_feedback", [])
    outcomes = rows.get("user_item_outcomes", [])
    traces = rows.get("listing_decision_traces", [])
    searches = rows.get("shared_scan_searches", [])
    search_results = rows.get("shared_scan_results", [])
    scan_status = Counter(str(row.get("status") or "unknown") for row in scans)
    notification_status = Counter(str(row.get("status") or "unknown") for row in notifications)
    push_status = Counter(str(row.get("status") or "unknown") for row in push)
    feedback_labels = Counter(str(row.get("label") or "unknown") for row in feedback)
    needs_data = sum(int(row.get("needs_data_count") or 0) for row in searches)
    detail_failures = sum(int(row.get("detail_fetch_failures") or 0) for row in searches)
    pricing_gaps = feedback_labels["pricing_wrong"] + sum(
        1 for row in outcomes if row.get("actual_net_profit") is None and str(row.get("status") or "") in {"sold", "completed"}
    )
    component_mistakes = sum(
        1 for row in feedback
        if str(row.get("label") or "") == "accessory_not_phone" and str(row.get("item_type") or "") != "component"
    )
    suspicious_accepts = sum(
        1 for row in feedback
        if str(row.get("label") or "") in {"accessory_not_phone", "not_profitable", "too_risky"}
        and float(row.get("estimated_profit") or 0) >= 50
    )
    suspicious_rejects = sum(
        1 for row in feedback
        if str(row.get("label") or "") == "good_deal" and float(row.get("estimated_profit") or 0) < 25
    )
    component_economics = 0
    for row in traces:
        try:
            trace = json.loads(row.get("trace_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        verdict = trace.get("verdict") or {}
        detected = trace.get("detected") or {}
        decision = trace.get("alert_decision") or {}
        is_component = detected.get("accessory_or_part_only") or verdict.get("item_type") == "component"
        if is_component and (decision.get("eligible") or float(decision.get("expected_profit") or 0) > 0):
            component_economics += 1

    recommendations: list[str] = []
    failed_scans = scan_status["failed"]
    if failed_scans:
        recommendations.append(f"Investigate {failed_scans} failed scan cycle(s), starting with the newest error category.")
    delivery_failures = notification_status["failed"] + push_status["failed"] + push_status["invalid"]
    if delivery_failures:
        recommendations.append(f"Review {delivery_failures} notification delivery failure(s); invalid push devices are disabled automatically.")
    negative_feedback = sum(count for label, count in feedback_labels.items() if label not in {"good_deal", "unknown"})
    if negative_feedback:
        recommendations.append(f"Review {negative_feedback} negative classification feedback row(s) before proposing scorer changes.")
    if component_mistakes or component_economics:
        recommendations.append(
            f"Inspect component protection evidence: {component_mistakes} user-corrected classification(s), {component_economics} trace(s) with complete-phone economics."
        )
    if suspicious_accepts or suspicious_rejects:
        recommendations.append(
            f"Replay ranking candidates: {suspicious_accepts} suspicious accept(s), {suspicious_rejects} suspicious reject(s)."
        )
    if needs_data or detail_failures:
        recommendations.append(f"Review search/detail gaps: {needs_data} needs-data results and {detail_failures} detail-fetch failures.")
    if pricing_gaps:
        recommendations.append(f"Inspect {pricing_gaps} pricing evidence gap(s) without changing production prices automatically.")

    profit_errors: list[float] = []
    for row in outcomes:
        estimated = row.get("estimated_profit")
        actual = row.get("actual_net_profit")
        if estimated is not None and actual is not None:
            profit_errors.append(float(actual) - float(estimated))
    if len(profit_errors) >= 3:
        mean_error = sum(profit_errors) / len(profit_errors)
        if abs(mean_error) >= 25:
            direction = "underestimated" if mean_error > 0 else "overestimated"
            recommendations.append(
                f"Actual profit {direction} estimates by ${abs(mean_error):,.2f} on average across {len(profit_errors)} new outcomes; inspect components before changing rules."
            )
    if not recommendations:
        recommendations.append("No new evidence crosses the current review thresholds.")

    newest_ids = {table: max((int(row["id"]) for row in table_rows), default=0) for table, table_rows in rows.items()}
    lines = [
        "# Notifierr Cloud Evidence Audit",
        "",
        f"Generated: {started_at.isoformat()}",
        "",
        "## New evidence",
        "",
        f"- Scan cycles: {len(scans)} ({dict(scan_status)})",
        f"- Notification attempts: {len(notifications)} ({dict(notification_status)})",
        f"- Push device events: {len(push)} ({dict(push_status)})",
        f"- User feedback: {len(feedback)} ({dict(feedback_labels)})",
        f"- Recorded outcomes: {len(outcomes)}",
        f"- Decision traces: {len(traces)}; component-economics anomalies: {component_economics}",
        f"- Search executions: {len(searches)}; result rows: {len(search_results)}; needs-data: {needs_data}; detail failures: {detail_failures}",
        f"- Suspicious accepts/rejects: {suspicious_accepts}/{suspicious_rejects}; pricing gaps: {pricing_gaps}",
        "",
        "## Recommendations",
        "",
        *[f"- {item}" for item in recommendations],
        "",
        "## Cursor evidence",
        "",
        *[f"- {table}: newest row {row_id}" for table, row_id in newest_ids.items()],
        "",
        "This report is observational. It did not rescore historical rows or write to the cloud database.",
    ]
    return "\n".join(lines) + "\n"


def run(database_url: str, *, state_path: Path, output_dir: Path, limit: int, advance: bool) -> Path:
    if not database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise ValueError("AUDIT_DATABASE_URL must be a PostgreSQL URL")
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    engine = sa.create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c application_name=notifierr_codex_auditor"},
    )
    cursor = _load_state(state_path)
    started_at = datetime.now(timezone.utc)
    rows: dict[str, list[dict[str, Any]]] = {}
    next_cursor = dict(cursor)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            tables = set(sa.inspect(connection).get_table_names())
            for table in TABLES:
                if table not in tables:
                    rows[table] = []
                    continue
                table_rows = _rows_since(connection, table, cursor.get(table, 0), limit)
                rows[table] = table_rows
                if table_rows:
                    next_cursor[table] = max(int(row["id"]) for row in table_rows)
            transaction.rollback()
        except Exception:
            transaction.rollback()
            raise
    engine.dispose()

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"cloud-evidence-{started_at.strftime('%Y%m%d-%H%M%S')}.md"
    report_path.write_text(_render_report(rows, started_at), encoding="utf-8")
    if advance:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"updated_at": started_at.isoformat(), "cursor": next_cursor}, indent=2) + "\n",
            encoding="utf-8",
        )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read new Notifierr cloud evidence without mutating production")
    parser.add_argument("--state", type=Path, default=Path("audit/cloud-audit-state.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("audit/cloud"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--no-advance", action="store_true")
    args = parser.parse_args()
    database_url = (os.getenv("AUDIT_DATABASE_URL") or "").strip()
    if not database_url:
        parser.error("AUDIT_DATABASE_URL is required and should use the dedicated read-only database role")
    report = run(
        database_url,
        state_path=args.state,
        output_dir=args.output_dir,
        limit=max(1, min(args.limit, 10000)),
        advance=not args.no_advance,
    )
    print(report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
