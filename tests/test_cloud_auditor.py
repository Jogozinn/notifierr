from pathlib import Path

import pytest

from backend.scripts.cloud_audit import _render_report, run
from datetime import datetime, timezone


def test_cloud_audit_rejects_non_postgres_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        run(
            "sqlite:///local.sqlite3",
            state_path=tmp_path / "state.json",
            output_dir=tmp_path,
            limit=10,
            advance=False,
        )


def test_cloud_audit_reports_review_candidates_without_mutation_language() -> None:
    report = _render_report(
        {
            "scan_cycles": [{"id": 1, "status": "failed"}],
            "notification_attempts": [{"id": 1, "status": "failed"}],
            "push_delivery_attempts": [{"id": 1, "status": "invalid"}],
            "user_item_feedback": [
                {"id": 1, "label": "accessory_not_phone", "item_type": "whole_phone", "estimated_profit": 80},
            ],
            "user_item_outcomes": [],
            "listing_decision_traces": [],
            "shared_scan_searches": [{"id": 1, "needs_data_count": 2, "detail_fetch_failures": 1}],
            "shared_scan_results": [],
        },
        datetime(2026, 10, 4, tzinfo=timezone.utc),
    )

    assert "component protection evidence" in report
    assert "suspicious accept" in report
    assert "did not rescore historical rows or write" in report
