"""Ownership and verified migration for user-controlled memory state."""

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
USER_MEMORY_DEFAULT_DB = ROOT / "runtime" / "user_memory.db"
USER_MEMORY_DB_ENV = "BJ_PAL_USER_MEMORY_DB"
USER_MEMORY_DOMAIN = "user_memory"
STATE_LAYOUT_VERSION = "state_layout_v1"

USER_MEMORY_TABLE_COLUMNS = {
    "user_memory": (
        "id",
        "user_id",
        "kind",
        "mem_key",
        "mem_value",
        "confidence",
        "mention_count",
        "first_seen_at",
        "last_seen_at",
        "forgotten",
        "source",
        "confirmed_at",
        "expires_at",
        "revision",
    ),
    "user_memory_events": (
        "event_id",
        "user_id",
        "kind",
        "mem_key",
        "event_type",
        "revision",
        "source",
        "value_sha256",
        "previous_value_sha256",
        "reason",
        "created_at",
    ),
}

USER_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,
    mem_key         TEXT NOT NULL,
    mem_value       TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.7,
    mention_count   INTEGER NOT NULL DEFAULT 1,
    first_seen_at   REAL NOT NULL,
    last_seen_at    REAL NOT NULL,
    forgotten       INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'legacy',
    confirmed_at    REAL,
    expires_at      REAL,
    revision        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, kind, mem_key)
);
CREATE INDEX IF NOT EXISTS idx_user_memory_user
ON user_memory(user_id, forgotten, expires_at);

CREATE TABLE IF NOT EXISTS user_memory_events (
    event_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    mem_key                TEXT NOT NULL,
    event_type             TEXT NOT NULL CHECK(event_type IN (
        'created', 'reinforced', 'replaced', 'conflict_rejected',
        'confirmed', 'forgotten'
    )),
    revision               INTEGER NOT NULL,
    source                 TEXT NOT NULL,
    value_sha256           TEXT,
    previous_value_sha256  TEXT,
    reason                 TEXT NOT NULL DEFAULT '',
    created_at             REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_memory_events_user
ON user_memory_events(user_id, event_id);
CREATE INDEX IF NOT EXISTS idx_user_memory_events_key
ON user_memory_events(user_id, kind, mem_key, event_id);

CREATE TRIGGER IF NOT EXISTS user_memory_events_no_update
BEFORE UPDATE ON user_memory_events
BEGIN
    SELECT RAISE(ABORT, 'user_memory_events are immutable');
END;
"""

USER_MEMORY_SPEC = DomainSpec(
    domain=USER_MEMORY_DOMAIN,
    layout_version=STATE_LAYOUT_VERSION,
    table_columns=USER_MEMORY_TABLE_COLUMNS,
    schema=USER_MEMORY_SCHEMA,
    order_columns={
        "user_memory": ("id",),
        "user_memory_events": ("event_id",),
    },
    legacy_column_defaults={
        ("user_memory", "source"): "'legacy'",
        ("user_memory", "confirmed_at"): "NULL",
        ("user_memory", "expires_at"): "NULL",
        ("user_memory", "revision"): "1",
    },
)


def inspect_user_memory_store(path: Path) -> dict[str, Any]:
    return inspect_store(path, USER_MEMORY_SPEC)


def resolve_user_memory_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get(USER_MEMORY_DB_ENV)
    if configured:
        return Path(configured)
    if metadata_valid(USER_MEMORY_DEFAULT_DB, USER_MEMORY_SPEC):
        return USER_MEMORY_DEFAULT_DB
    if legacy_has_rows(LEGACY_SHARED_DB, USER_MEMORY_SPEC):
        return LEGACY_SHARED_DB
    return USER_MEMORY_DEFAULT_DB


def ensure_user_memory_metadata(path: Path, *, origin: str = "native") -> None:
    path = Path(path)
    if path.resolve() == LEGACY_SHARED_DB.resolve():
        return
    ensure_metadata(path, USER_MEMORY_SPEC, origin=origin)


def migrate_user_memory_store(
    *,
    source: Path = LEGACY_SHARED_DB,
    destination: Path = USER_MEMORY_DEFAULT_DB,
    apply: bool = False,
) -> dict[str, Any]:
    return migrate_store(
        source=source,
        destination=destination,
        spec=USER_MEMORY_SPEC,
        apply=apply,
    )
