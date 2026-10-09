from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx

from backend.scripts import research_bundle_sync


def _bundle(day: str, records: int = 1) -> bytes:
    lines = [json.dumps({
        "type": "summary", "schema": 1, "day": day,
        "records": records, "interesting_records": records,
    })]
    for index in range(records):
        lines.append(json.dumps({"type": "evidence", "id": index + 1, "evidence": {"item_id": f"x-{index}"}}))
    return gzip.compress(("\n".join(lines) + "\n").encode())


def test_sync_uses_update_cursor_and_redownloads_changed_day(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    output_dir = tmp_path / "bundles"
    seen_updated_after: list[str] = []
    index_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal index_calls
        if request.url.path == "/admin/research/bundles":
            index_calls += 1
            cursor = request.url.params.get("updated_after", "")
            seen_updated_after.append(cursor)
            if index_calls == 1:
                return httpx.Response(200, json={
                    "bundles": [{"day": "2026-10-07", "records": 1, "last_id": 1, "updated_at": "2026-10-09T18:00:00+00:00"}],
                    "cursor": "2026-10-09T18:00:00+00:00",
                })
            return httpx.Response(200, json={
                # Same listing day changed later because human feedback arrived.
                "bundles": [{"day": "2026-10-07", "records": 2, "last_id": 2, "updated_at": "2026-10-09T20:00:00+00:00"}],
                "cursor": "2026-10-09T20:00:00+00:00",
            })
        if request.url.path == "/admin/research/bundles/2026-10-07":
            return httpx.Response(200, content=_bundle("2026-10-07", records=index_calls))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(research_bundle_sync.httpx, "Client", client_factory)

    first = research_bundle_sync.run(
        api_url="https://notifierr.example",
        token="x" * 32,
        state_path=state_path,
        output_dir=output_dir,
    )
    second = research_bundle_sync.run(
        api_url="https://notifierr.example",
        token="x" * 32,
        state_path=state_path,
        output_dir=output_dir,
    )

    assert seen_updated_after == ["", "2026-10-09T18:00:00+00:00"]
    assert first["last_updated_at"] == "2026-10-09T18:00:00+00:00"
    assert second["last_updated_at"] == "2026-10-09T20:00:00+00:00"
    target = output_dir / "notifierr-research-2026-10-07.jsonl.gz"
    summary = research_bundle_sync._validate_bundle(target.read_bytes())
    assert summary["records"] == 2
    state = json.loads(state_path.read_text())
    assert state["last_updated_at"] == "2026-10-09T20:00:00+00:00"
