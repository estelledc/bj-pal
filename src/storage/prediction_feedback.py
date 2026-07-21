"""Ownership and verified migration for wait-prediction feedback state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .verified_copy import (
    DomainSpec,
    ensure_metadata,
    inspect_store,
    legacy_has_rows,
    metadata_valid,
    migrate_store,
)


ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_SHARED_DB = ROOT / "tool_calls.db"
PREDICTION_FEEDBACK_DEFAULT_DB = ROOT / "runtime" / "prediction_feedback.db"
PREDICTION_FEEDBACK_DB_ENV = "BJ_PAL_PREDICTION_DB"
PREDICTION_FEEDBACK_DOMAIN = "prediction_feedback"
STATE_LAYOUT_VERSION = "state_layout_v1"

PREDICTION_FEEDBACK_TABLE_COLUMNS = {
    "prediction_log": (
        "id", "poi_name", "target_time", "predicted_wait_min", "predicted_at",
        "actual_wait_min", "actual_at", "confidence",
    )
}

PREDICTION_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS prediction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poi_name TEXT NOT NULL,
    target_time TEXT,
    predicted_wait_min INTEGER,
    predicted_at TEXT,
    actual_wait_min INTEGER DEFAULT NULL,
    actual_at TEXT DEFAULT NULL,
    confidence REAL DEFAULT 0.8
);
CREATE INDEX IF NOT EXISTS idx_pred_poi ON prediction_log(poi_name);
"""

PREDICTION_FEEDBACK_SPEC = DomainSpec(
    domain=PREDICTION_FEEDBACK_DOMAIN,
    layout_version=STATE_LAYOUT_VERSION,
    table_columns=PREDICTION_FEEDBACK_TABLE_COLUMNS,
    schema=PREDICTION_FEEDBACK_SCHEMA,
)


def inspect_prediction_feedback_store(path: Path) -> dict[str, Any]:
    return inspect_store(path, PREDICTION_FEEDBACK_SPEC)


def resolve_prediction_feedback_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get(PREDICTION_FEEDBACK_DB_ENV)
    if configured:
        return Path(configured)
    if metadata_valid(PREDICTION_FEEDBACK_DEFAULT_DB, PREDICTION_FEEDBACK_SPEC):
        return PREDICTION_FEEDBACK_DEFAULT_DB
    if legacy_has_rows(LEGACY_SHARED_DB, PREDICTION_FEEDBACK_SPEC):
        return LEGACY_SHARED_DB
    return PREDICTION_FEEDBACK_DEFAULT_DB


def ensure_prediction_feedback_metadata(
    path: Path,
    *,
    origin: str = "native",
) -> None:
    path = Path(path)
    if path.resolve() == LEGACY_SHARED_DB.resolve():
        return
    ensure_metadata(path, PREDICTION_FEEDBACK_SPEC, origin=origin)


def migrate_prediction_feedback_store(
    *,
    source: Path = LEGACY_SHARED_DB,
    destination: Path = PREDICTION_FEEDBACK_DEFAULT_DB,
    apply: bool = False,
) -> dict[str, Any]:
    return migrate_store(
        source=source,
        destination=destination,
        spec=PREDICTION_FEEDBACK_SPEC,
        apply=apply,
    )
