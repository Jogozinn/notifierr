from __future__ import annotations

import gzip
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main
from backend.config import Settings
from backend.storage import Storage


def test_research_bundle_api_supports_dedicated_read_only_token(monkeypatch, tmp_path: Path) -> None:
    storage = Storage(tmp_path / "research-api.sqlite3")
    user = storage.create_user(email="owner@example.com", password_hash="unused", role="admin", baseline_keywords=[])
    storage.upsert_research_evidence(
        user_id=int(user["id"]),
        marketplace_item_id=77,
        item_id="ebay-77",
        evidence_day="2026-10-08",
        interesting=True,
        evidence={"schema": 1, "item_id": "ebay-77", "expected_net_profit": 125},
        timestamp="2026-10-09T18:00:00+00:00",
    )
    settings = Settings(
        auth_required=True,
        auth_secret_key="test-auth-secret-value-is-long-enough",
        research_export_token="research-token-value-long-enough",
        background_poll_enabled=False,
    )
    monkeypatch.setattr(main, "storage", storage)
    monkeypatch.setattr(main, "settings", settings)

    headers = {"X-Notifierr-Research-Token": settings.research_export_token}
    with TestClient(main.app) as client:
        unauthorized = client.get("/admin/research/bundles")
        assert unauthorized.status_code in {401, 403}

        index = client.get("/admin/research/bundles", headers=headers)
        assert index.status_code == 200, index.text
        payload = index.json()
        assert payload["bundles"][0]["day"] == "2026-10-08"
        assert payload["bundles"][0]["records"] == 1
        assert payload["cursor"] == "2026-10-09T18:00:00+00:00"

        bundle = client.get("/admin/research/bundles/2026-10-08", headers=headers)
        assert bundle.status_code == 200, bundle.text
        assert bundle.headers["content-type"].startswith("application/gzip")
        lines = gzip.decompress(bundle.content).decode("utf-8").strip().splitlines()
        summary = json.loads(lines[0])
        evidence = json.loads(lines[1])
        assert summary["type"] == "summary"
        assert summary["records"] == 1
        assert evidence["type"] == "evidence"
        assert evidence["evidence"]["expected_net_profit"] == 125
