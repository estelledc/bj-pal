"""Reusable, fail-closed SQLite snapshot migration for owned state domains."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS state_store_metadata (
    domain                   TEXT PRIMARY KEY,
    layout_version           TEXT NOT NULL,
    origin                   TEXT NOT NULL CHECK(origin IN ('native', 'migrated_copy')),
    source_name              TEXT,
    source_counts_json       TEXT NOT NULL,
    source_digests_json      TEXT NOT NULL,
    destination_counts_json  TEXT NOT NULL,
    destination_digests_json TEXT NOT NULL,
    recorded_at              TEXT NOT NULL,
    receipt_sha256           TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class DomainSpec:
    domain: str
    layout_version: str
    table_columns: Mapping[str, tuple[str, ...]]
    schema: str
    order_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    legacy_column_defaults: Mapping[tuple[str, str], str] = field(
        default_factory=dict
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _select_expression(
    connection: sqlite3.Connection,
    spec: DomainSpec,
    table: str,
    columns: tuple[str, ...],
) -> str:
    available = _table_columns(connection, table)
    missing = {
        column
        for column in columns
        if column not in available and (table, column) not in spec.legacy_column_defaults
    }
    if missing:
        raise ValueError(f"legacy {table} is missing required columns: {sorted(missing)}")
    return ", ".join(
        column
        if column in available
        else f"{spec.legacy_column_defaults[(table, column)]} AS {column}"
        for column in columns
    )


def _logical_digest(
    connection: sqlite3.Connection,
    spec: DomainSpec,
    table: str,
    columns: tuple[str, ...],
) -> str:
    select = _select_expression(connection, spec, table, columns)
    order_by = _order_by(spec, table, columns)
    digest = hashlib.sha256()
    cursor = connection.execute(f"SELECT {select} FROM {table} ORDER BY {order_by}")
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        for row in rows:
            digest.update(canonical_json(list(row)).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _order_by(
    spec: DomainSpec,
    table: str,
    columns: tuple[str, ...],
) -> str:
    order_columns = spec.order_columns.get(table, ("id",))
    unknown = set(order_columns) - set(columns)
    if not order_columns or unknown:
        raise ValueError(
            f"{spec.domain} has invalid stable order columns for {table}: "
            f"{sorted(unknown)}"
        )
    return ", ".join(order_columns)


def snapshot(
    connection: sqlite3.Connection,
    spec: DomainSpec,
) -> dict[str, dict[str, Any]]:
    missing = set(spec.table_columns) - table_names(connection)
    if missing:
        raise ValueError(f"{spec.domain} source is missing tables: {sorted(missing)}")
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for table, columns in spec.table_columns.items():
        counts[table] = int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        )
        digests[table] = _logical_digest(connection, spec, table, columns)
    return {"counts": counts, "digests": digests}


def inspect_store(path: Path, spec: DomainSpec) -> dict[str, Any]:
    """Return only counts and logical hashes; never return stored payloads."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with connect_read_only(path) as connection:
        connection.execute("BEGIN")
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        current = snapshot(connection, spec)
        connection.rollback()
    return {
        "domain": spec.domain,
        "layout_version": spec.layout_version,
        "database_name": path.name,
        "journal_mode": journal_mode,
        **current,
    }


def metadata_body(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return {
        "domain": row["domain"],
        "layout_version": row["layout_version"],
        "origin": row["origin"],
        "source_name": row["source_name"],
        "source_counts": json.loads(row["source_counts_json"]),
        "source_digests": json.loads(row["source_digests_json"]),
        "destination_counts": json.loads(row["destination_counts_json"]),
        "destination_digests": json.loads(row["destination_digests_json"]),
        "recorded_at": row["recorded_at"],
    }


def metadata_valid(path: Path, spec: DomainSpec) -> bool:
    if not Path(path).is_file():
        return False
    try:
        with connect_read_only(path) as connection:
            if "state_store_metadata" not in table_names(connection):
                return False
            row = connection.execute(
                "SELECT * FROM state_store_metadata WHERE domain=?",
                (spec.domain,),
            ).fetchone()
            return bool(
                row
                and row["layout_version"] == spec.layout_version
                and row["receipt_sha256"] == canonical_sha256(metadata_body(row))
            )
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError):
        return False


def legacy_has_rows(path: Path, spec: DomainSpec) -> bool:
    if not Path(path).is_file():
        return False
    try:
        with connect_read_only(path) as connection:
            if not set(spec.table_columns) <= table_names(connection):
                return False
            return any(
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in spec.table_columns
            )
    except sqlite3.Error:
        return False


def _insert_metadata(
    connection: sqlite3.Connection,
    spec: DomainSpec,
    *,
    origin: str,
    source_name: str | None,
    source_snapshot: Mapping[str, Mapping[str, Any]],
    destination_snapshot: Mapping[str, Mapping[str, Any]],
) -> None:
    recorded_at = datetime.now(timezone.utc).isoformat()
    body = {
        "domain": spec.domain,
        "layout_version": spec.layout_version,
        "origin": origin,
        "source_name": source_name,
        "source_counts": source_snapshot["counts"],
        "source_digests": source_snapshot["digests"],
        "destination_counts": destination_snapshot["counts"],
        "destination_digests": destination_snapshot["digests"],
        "recorded_at": recorded_at,
    }
    connection.execute(
        """
        INSERT INTO state_store_metadata(
            domain, layout_version, origin, source_name,
            source_counts_json, source_digests_json,
            destination_counts_json, destination_digests_json,
            recorded_at, receipt_sha256
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            spec.domain,
            spec.layout_version,
            origin,
            source_name,
            canonical_json(source_snapshot["counts"]),
            canonical_json(source_snapshot["digests"]),
            canonical_json(destination_snapshot["counts"]),
            canonical_json(destination_snapshot["digests"]),
            recorded_at,
            canonical_sha256(body),
        ),
    )


def ensure_metadata(path: Path, spec: DomainSpec, *, origin: str = "native") -> None:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(METADATA_SCHEMA)
        existing = connection.execute(
            "SELECT * FROM state_store_metadata WHERE domain=?",
            (spec.domain,),
        ).fetchone()
        if existing is not None:
            if existing["receipt_sha256"] != canonical_sha256(metadata_body(existing)):
                raise ValueError(f"{spec.domain} metadata receipt is invalid")
            return
        current = snapshot(connection, spec)
        _insert_metadata(
            connection,
            spec,
            origin=origin,
            source_name=None,
            source_snapshot={"counts": {}, "digests": {}},
            destination_snapshot=current,
        )
        connection.commit()


def migrate_store(
    *,
    source: Path,
    destination: Path,
    spec: DomainSpec,
    apply: bool = False,
) -> dict[str, Any]:
    """Copy one consistent domain snapshot and never delete the source."""
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("source and destination must be different databases")
    before_file_sha = file_sha256(source)
    inspected = inspect_store(source, spec)
    preview = {
        "domain": spec.domain,
        "layout_version": spec.layout_version,
        "mode": "apply" if apply else "dry_run",
        "source_name": source.name,
        "destination_name": destination.name,
        "source_counts": inspected["counts"],
        "source_digests": inspected["digests"],
        "source_journal_mode": inspected["journal_mode"],
        "legacy_source_modified": False,
    }
    if not apply:
        preview["preview_sha256"] = canonical_sha256(preview)
        return preview
    if str(inspected["journal_mode"]).lower() == "wal":
        raise RuntimeError("WAL sources require an explicit checkpointed backup workflow")
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        with connect_read_only(source) as source_connection:
            source_connection.execute("BEGIN")
            stable_source = snapshot(source_connection, spec)
            expected_source = {
                "counts": inspected["counts"],
                "digests": inspected["digests"],
            }
            if stable_source != expected_source:
                raise RuntimeError("legacy source changed between preflight and copy")
            with sqlite3.connect(temporary) as destination_connection:
                destination_connection.row_factory = sqlite3.Row
                destination_connection.executescript(spec.schema)
                destination_connection.executescript(METADATA_SCHEMA)
                for table, columns in spec.table_columns.items():
                    select = _select_expression(
                        source_connection, spec, table, columns
                    )
                    order_by = _order_by(spec, table, columns)
                    source_cursor = source_connection.execute(
                        f"SELECT {select} FROM {table} ORDER BY {order_by}"
                    )
                    placeholders = ",".join("?" for _ in columns)
                    column_sql = ",".join(columns)
                    while True:
                        rows = source_cursor.fetchmany(1000)
                        if not rows:
                            break
                        destination_connection.executemany(
                            f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})",
                            [tuple(row) for row in rows],
                        )
                destination_snapshot = snapshot(destination_connection, spec)
                if destination_snapshot != stable_source:
                    raise RuntimeError("destination counts or logical hashes do not match")
                _insert_metadata(
                    destination_connection,
                    spec,
                    origin="migrated_copy",
                    source_name=source.name,
                    source_snapshot=stable_source,
                    destination_snapshot=destination_snapshot,
                )
                destination_connection.commit()
                quick_check = destination_connection.execute(
                    "PRAGMA quick_check"
                ).fetchone()[0]
                if quick_check != "ok":
                    raise RuntimeError(
                        f"destination quick_check failed: {quick_check}"
                    )
            source_connection.rollback()
        if file_sha256(source) != before_file_sha:
            raise RuntimeError("legacy source bytes changed during migration")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    result = {
        **preview,
        "destination_counts": inspected["counts"],
        "destination_digests": inspected["digests"],
        "legacy_source_modified": False,
        "destination_quick_check": "ok",
        "receipt_valid": metadata_valid(destination, spec),
    }
    result["migration_sha256"] = canonical_sha256(result)
    return result
