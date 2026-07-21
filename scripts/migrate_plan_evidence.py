#!/usr/bin/env python3
"""Preflight or apply the non-destructive plan-evidence store migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from storage.state_layout import (  # noqa: E402
    LEGACY_SHARED_DB,
    PLAN_EVIDENCE_DEFAULT_DB,
    migrate_plan_evidence_store,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy plan_trace/plan_outcome into the dedicated runtime store. "
            "Dry-run is the default; the legacy database is never deleted."
        )
    )
    parser.add_argument("--source", type=Path, default=LEGACY_SHARED_DB)
    parser.add_argument("--destination", type=Path, default=PLAN_EVIDENCE_DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-domain",
        help="Required with --apply; must equal plan_evidence.",
    )
    args = parser.parse_args()
    if args.apply and args.confirm_domain != "plan_evidence":
        parser.error("--apply requires --confirm-domain plan_evidence")
    return args


def main() -> int:
    args = parse_args()
    result = migrate_plan_evidence_store(
        source=args.source,
        destination=args.destination,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
