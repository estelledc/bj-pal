from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from agents import user_memory as memory_api
from storage import user_memory as memory_storage


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(memory_storage.USER_MEMORY_SCHEMA)
        connection.executemany(
            """
            INSERT INTO user_memory(
                id, user_id, kind, mem_key, mem_value, confidence,
                mention_count, first_seen_at, last_seen_at, forgotten, source,
                confirmed_at, expires_at, revision
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (7, "user-a", "fact", "area:city", '"北京"', 0.8, 1,
                 1.0, 2.0, 0, "explicit_user_input", 2.0, None, 1),
                (11, "user-b", "preference", "taste:coffee", "true", 0.7,
                 2, 3.0, 4.0, 1, "manual_entry", 3.0, None, 1),
            ],
        )
        connection.executemany(
            """
            INSERT INTO user_memory_events(
                event_id, user_id, kind, mem_key, event_type, revision, source,
                value_sha256, previous_value_sha256, reason, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (13, "user-a", "fact", "area:city", "created", 1,
                 "explicit_user_input", "a" * 64, None, "new_memory", 2.0),
                (21, "user-b", "preference", "taste:coffee", "forgotten", 1,
                 "manual_entry", "b" * 64, None, "user_soft_forget", 4.0),
            ],
        )
        connection.executescript(
            """
            CREATE TABLE prediction_log(id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO prediction_log VALUES (1, 'private-prediction-marker');
            """
        )


def _configure(
    source: Path,
    destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_storage, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(memory_storage, "USER_MEMORY_DEFAULT_DB", destination)
    monkeypatch.setattr(memory_api, "_DB_PATH", None)
    monkeypatch.delenv(memory_storage.USER_MEMORY_DB_ENV, raising=False)


def test_user_memory_dry_run_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "user_memory.db"
    _source(source)
    before = _sha(source)

    result = memory_storage.migrate_user_memory_store(
        source=source, destination=destination
    )

    assert result["mode"] == "dry_run"
    assert result["source_counts"] == {
        "user_memory": 2,
        "user_memory_events": 2,
    }
    assert _sha(source) == before
    assert not destination.exists()


def test_user_memory_apply_preserves_sparse_ids_and_domain_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "user_memory.db"
    _source(source)
    before = _sha(source)

    result = memory_storage.migrate_user_memory_store(
        source=source, destination=destination, apply=True
    )

    assert result["receipt_valid"] is True
    assert result["source_digests"] == result["destination_digests"]
    assert _sha(source) == before
    with sqlite3.connect(destination) as connection:
        memory_ids = connection.execute(
            "SELECT id FROM user_memory ORDER BY id"
        ).fetchall()
        event_ids = connection.execute(
            "SELECT event_id FROM user_memory_events ORDER BY event_id"
        ).fetchall()
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert memory_ids == [(7,), (11,)]
    assert event_ids == [(13,), (21,)]
    assert tables == {"user_memory", "user_memory_events", "state_store_metadata"}
    assert destination.stat().st_mode & 0o777 == 0o600
    assert b"private-prediction-marker" not in destination.read_bytes()


def test_user_memory_resolver_stays_legacy_until_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "user_memory.db"
    _source(source)
    _configure(source, destination, monkeypatch)

    assert memory_api.database_path() == source
    memory_storage.migrate_user_memory_store(
        source=source, destination=destination, apply=True
    )
    assert memory_api.database_path() == destination


def test_user_memory_mutations_only_touch_migrated_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "user_memory.db"
    _source(source)
    memory_storage.migrate_user_memory_store(
        source=source, destination=destination, apply=True
    )
    _configure(source, destination, monkeypatch)
    before = _sha(source)

    replaced = memory_api.upsert_memory(
        "user-a",
        "area:city",
        "上海",
        kind="fact",
        source="explicit_user_input",
        confirmed=True,
    )
    assert replaced.action == "replaced"
    assert memory_api.delete_all("user-b") == 1

    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT user_id, mem_value, revision FROM user_memory ORDER BY id"
        ).fetchall()
        events = connection.execute(
            "SELECT user_id, event_type FROM user_memory_events ORDER BY event_id"
        ).fetchall()
    assert rows == [("user-a", '"上海"', 2)]
    assert events[-1] == ("user-a", "replaced")
    assert all(row[0] != "user-b" for row in events)
    assert _sha(source) == before


def test_user_memory_event_update_trigger_survives_copy(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "user_memory.db"
    _source(source)
    memory_storage.migrate_user_memory_store(
        source=source, destination=destination, apply=True
    )

    with sqlite3.connect(destination) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE user_memory_events SET reason='tampered' WHERE event_id=13"
            )


def test_user_memory_wal_source_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "user_memory.db"
    _source(source)
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    with pytest.raises(RuntimeError, match="WAL sources"):
        memory_storage.migrate_user_memory_store(
            source=source, destination=destination, apply=True
        )
    assert not destination.exists()


def test_user_memory_destination_must_not_preexist(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "user_memory.db"
    _source(source)
    destination.touch()

    with pytest.raises(FileExistsError):
        memory_storage.migrate_user_memory_store(
            source=source, destination=destination, apply=True
        )
