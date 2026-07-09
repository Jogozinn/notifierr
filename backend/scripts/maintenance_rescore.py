from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.maintenance_rescore import MaintenanceRescoreRequest, run_maintenance_rescore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely rescore stored Notifierr item states from stored raw listing data.")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--item-id", action="append", default=[], help="Item id to rescore. Repeat or pass comma-separated ids.")
    parser.add_argument("--source-cycle-id", type=int)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--non-stale-only", action="store_true")
    parser.add_argument("--not-ignored-only", action="store_true")
    parser.add_argument("--include-candidates", action="store_true")
    parser.add_argument("--include-risky", action="store_true")
    parser.add_argument("--include-needs-data", action="store_true")
    parser.add_argument("--include-rejected", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="write", action="store_false", help="Preview changes without updating user_item_states.")
    mode.add_argument("--write", dest="write", action="store_true", help="Explicitly update scorer-derived user_item_states fields.")
    parser.set_defaults(write=False)
    parser.add_argument("--write-traces", action="store_true", help="In write mode, persist decision traces for rescored rows.")
    parser.add_argument("--reason", default="maintenance_rescore")
    parser.add_argument("--report-dir", default="audit")
    return parser


def request_from_args(args: argparse.Namespace) -> MaintenanceRescoreRequest:
    return MaintenanceRescoreRequest(
        user_id=int(args.user_id),
        item_ids=list(args.item_id or []),
        source_cycle_id=args.source_cycle_id,
        limit=int(args.limit or 100),
        active_only=bool(args.active_only),
        non_stale_only=bool(args.non_stale_only),
        not_ignored_only=bool(args.not_ignored_only),
        include_candidates=bool(args.include_candidates),
        include_risky=bool(args.include_risky),
        include_needs_data=bool(args.include_needs_data),
        include_rejected=bool(args.include_rejected),
        write=bool(args.write),
        write_traces=bool(args.write_traces),
        reason=str(args.reason or "maintenance_rescore"),
        report_dir=Path(args.report_dir),
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = run_maintenance_rescore(request_from_args(args))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
