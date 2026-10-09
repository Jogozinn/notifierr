"""Fetch compact Notifierr research bundles from the cloud API.

This is intentionally independent of the live scanner.  It is designed to run on
Jogo's PC when available, download only unprocessed daily JSONL.gz bundles, and
leave them on disk for a later Codex review.

Required environment variables:
  NOTIFIERR_API_URL=https://...               (public API base)
  NOTIFIERR_RESEARCH_TOKEN=<dedicated token>  (same value as RESEARCH_EXPORT_TOKEN)
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"last_day": "", "last_updated_at": "", "downloaded": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_day": "", "last_updated_at": "", "downloaded": []}
    return {
        "last_day": str(payload.get("last_day") or ""),
        "last_updated_at": str(payload.get("last_updated_at") or ""),
        "downloaded": list(payload.get("downloaded") or []),
    }


def _validate_bundle(data: bytes) -> dict[str, Any]:
    text = gzip.decompress(data).decode("utf-8")
    first = next((line for line in text.splitlines() if line.strip()), "")
    if not first:
        raise ValueError("Bundle is empty")
    summary = json.loads(first)
    if summary.get("type") != "summary" or int(summary.get("schema") or 0) != 1:
        raise ValueError("Unexpected research bundle format")
    return summary


def run(*, api_url: str, token: str, state_path: Path, output_dir: Path, interesting_only: bool = False) -> dict[str, Any]:
    base = api_url.rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("NOTIFIERR_API_URL must use HTTPS")
    if len(token.strip()) < 16:
        raise ValueError("NOTIFIERR_RESEARCH_TOKEN is missing or too short")
    state = _load_state(state_path)
    headers = {"X-Notifierr-Research-Token": token.strip()}
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []
    with httpx.Client(timeout=45, headers=headers, follow_redirects=True) as client:
        response = client.get(
            f"{base}/admin/research/bundles",
            params={"updated_after": state.get("last_updated_at") or "", "limit": 365},
        )
        response.raise_for_status()
        index_payload = response.json()
        bundles = list(index_payload.get("bundles") or [])
        for bundle in bundles:
            day = str(bundle.get("day") or "")
            if not day:
                continue
            target = output_dir / f"notifierr-research-{day}.jsonl.gz"
            bundle_response = client.get(
                f"{base}/admin/research/bundles/{day}",
                params={"interesting_only": str(bool(interesting_only)).lower()},
            )
            bundle_response.raise_for_status()
            summary = _validate_bundle(bundle_response.content)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(bundle_response.content)
            temporary.replace(target)
            downloaded.append({
                "day": day,
                "path": str(target),
                "records": int(summary.get("records") or 0),
                "interesting_records": int(summary.get("interesting_records") or 0),
                "bytes": len(bundle_response.content),
            })
            state["last_day"] = max(str(state.get("last_day") or ""), day)
        # Advance only after every changed bundle in this index response was written.
        if index_payload.get("cursor"):
            state["last_updated_at"] = str(index_payload["cursor"])
    if downloaded:
        state["downloaded"] = (list(state.get("downloaded") or []) + downloaded)[-180:]
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    manifest = output_dir / "latest-manifest.json"
    manifest.write_text(json.dumps({
        "updated_at": state["updated_at"],
        "last_day": state.get("last_day") or "",
        "last_updated_at": state.get("last_updated_at") or "",
        "downloaded_this_run": downloaded,
        "bundle_directory": str(output_dir.resolve()),
        "codex_note": "Review only these compact bundles; do not connect Codex to the live production database.",
    }, indent=2) + "\n", encoding="utf-8")
    prompt_path = output_dir / "codex-review-prompt.md"
    prompt_path.write_text(
        """# Notifierr periodic research review

Read `latest-manifest.json`. If `downloaded_this_run` is empty, stop without making recommendations. For each listed `.jsonl.gz` bundle, decompress and analyze the compact evidence records.

Focus on: false negatives and missed opportunities; REVIEW/NEEDS_DATA items that look stronger than the deterministic decision; repeated blocking reasons; model/storage classification errors; repair-cost misses; resale-price drift; notification misses; human GOOD/BAD/UNSURE feedback; and actual PURCHASED/SOLD/FAILED_REPAIR outcomes. Compare findings against the current deterministic rules and pricing fingerprints.

Do not connect to the production database, do not call paid/cloud AI from Notifierr, and do not modify production rules automatically. Write evidence-backed recommendations to `audit/recommendations/YYYY-MM-DD.md`, including sample sizes, affected models/rules, expected benefit, risks, and a proposed validation test. Preserve failed or rejected hypotheses so they are not repeatedly rediscovered.
""",
        encoding="utf-8",
    )
    return {
        "downloaded": downloaded,
        "last_day": state.get("last_day") or "",
        "last_updated_at": state.get("last_updated_at") or "",
        "manifest": str(manifest),
        "codex_prompt": str(prompt_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("audit/research-fetch-state.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("audit/research-bundles"))
    parser.add_argument("--interesting-only", action="store_true")
    args = parser.parse_args()
    api_url = (os.getenv("NOTIFIERR_API_URL") or "").strip()
    token = (os.getenv("NOTIFIERR_RESEARCH_TOKEN") or "").strip()
    if not api_url or not token:
        parser.error("NOTIFIERR_API_URL and NOTIFIERR_RESEARCH_TOKEN are required")
    result = run(
        api_url=api_url,
        token=token,
        state_path=args.state,
        output_dir=args.output_dir,
        interesting_only=args.interesting_only,
    )
    print(json.dumps({"event": "research_bundle_sync_completed", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
