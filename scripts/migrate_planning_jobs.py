#!/usr/bin/env python3
"""Preflight, apply, or verify SQLite-to-PostgreSQL job-store cutover."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs.factory import POSTGRES_DSN_ENV, POSTGRES_SCHEMA_ENV  # noqa: E402
from jobs.migration import (  # noqa: E402
    JobStoreMigrationError,
    migrate_job_store,
    verify_job_store_cutover,
)
from jobs.repository import DEFAULT_JOB_DB, JobStoreUnavailable  # noqa: E402


CUTOVER_CONFIRMATION = "planning-jobs-sqlite-to-postgres"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Non-destructively copy the current SQLite planning-job store to an "
            "empty PostgreSQL schema. Dry-run is the default; the DSN is read "
            f"only from {POSTGRES_DSN_ENV}."
        )
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_JOB_DB)
    parser.add_argument(
        "--schema",
        default=os.environ.get(POSTGRES_SCHEMA_ENV, "public"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify-cutover", action="store_true")
    parser.add_argument(
        "--confirm-cutover",
        help=f"Required with --apply; must equal {CUTOVER_CONFIRMATION}.",
    )
    parser.add_argument(
        "--confirm-source-quiesced",
        action="store_true",
        help="Attest that every API and worker writing the SQLite store is stopped.",
    )
    args = parser.parse_args()
    if args.apply and args.confirm_cutover != CUTOVER_CONFIRMATION:
        parser.error(f"--apply requires --confirm-cutover {CUTOVER_CONFIRMATION}")
    if args.apply and not args.confirm_source_quiesced:
        parser.error("--apply requires --confirm-source-quiesced")
    return args


def main() -> int:
    args = parse_args()
    dsn = os.environ.get(POSTGRES_DSN_ENV, "").strip()
    if not dsn:
        raise SystemExit(f"{POSTGRES_DSN_ENV} is required")
    try:
        if args.verify_cutover:
            result = verify_job_store_cutover(
                source=args.source,
                dsn=dsn,
                schema=args.schema,
            )
        else:
            result = migrate_job_store(
                source=args.source,
                dsn=dsn,
                schema=args.schema,
                apply=args.apply,
                confirm_source_quiesced=args.confirm_source_quiesced,
            )
    except (JobStoreMigrationError, JobStoreUnavailable, ValueError, OSError) as exc:
        print(f"job-store migration failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
