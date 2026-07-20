from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from storage import prediction_feedback
from tools import prediction_log


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(prediction_feedback.PREDICTION_FEEDBACK_SCHEMA)
        connection.executemany(
            """
            INSERT INTO prediction_log(
                id, poi_name, target_time, predicted_wait_min, predicted_at,
                actual_wait_min, actual_at, confidence
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (7, "poi-a", "14:00", 10, "2026-01-01T13:00:00", None, None, 0.8),
                (11, "poi-b", "15:00", 20, "2026-01-01T14:00:00", 35,
                 "2026-01-01T16:00:00", 0.5),
            ],
        )
        connection.executescript(
            """
            CREATE TABLE user_memory(id INTEGER PRIMARY KEY, payload TEXT);
            INSERT INTO user_memory VALUES (1, 'private-memory-marker');
            """
        )


def test_prediction_dry_run_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "prediction_feedback.db"
    _source(source)
    before = _sha(source)

    result = prediction_feedback.migrate_prediction_feedback_store(
        source=source, destination=destination
    )

    assert result["mode"] == "dry_run"
    assert result["source_counts"] == {"prediction_log": 2}
    assert _sha(source) == before
    assert not destination.exists()


def test_prediction_apply_preserves_ids_nulls_and_domain_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "prediction_feedback.db"
    _source(source)
    before = _sha(source)

    result = prediction_feedback.migrate_prediction_feedback_store(
        source=source, destination=destination, apply=True
    )

    assert result["receipt_valid"] is True
    assert result["source_digests"] == result["destination_digests"]
    assert _sha(source) == before
    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT id, actual_wait_min FROM prediction_log ORDER BY id"
        ).fetchall()
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert rows == [(7, None), (11, 35)]
    assert tables == {"prediction_log", "state_store_metadata"}
    assert destination.stat().st_mode & 0o777 == 0o600
    assert b"private-memory-marker" not in destination.read_bytes()


def test_prediction_resolver_stays_legacy_until_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "prediction_feedback.db"
    _source(source)
    monkeypatch.setattr(prediction_feedback, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(
        prediction_feedback, "PREDICTION_FEEDBACK_DEFAULT_DB", destination
    )
    monkeypatch.setattr(prediction_log, "LOG_DB", None)
    monkeypatch.delenv(prediction_feedback.PREDICTION_FEEDBACK_DB_ENV, raising=False)

    assert prediction_log.database_path() == source
    prediction_feedback.migrate_prediction_feedback_store(
        source=source, destination=destination, apply=True
    )
    assert prediction_log.database_path() == destination


def test_prediction_update_and_delete_only_touch_migrated_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "prediction_feedback.db"
    _source(source)
    prediction_feedback.migrate_prediction_feedback_store(
        source=source, destination=destination, apply=True
    )
    monkeypatch.setattr(prediction_feedback, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(
        prediction_feedback, "PREDICTION_FEEDBACK_DEFAULT_DB", destination
    )
    monkeypatch.setattr(prediction_log, "LOG_DB", None)
    monkeypatch.delenv(prediction_feedback.PREDICTION_FEEDBACK_DB_ENV, raising=False)
    before = _sha(source)

    assert prediction_log.record_actual("poi-a", 42, "14:00") is True
    assert prediction_log.clear_history("poi-b") == 1

    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT poi_name, actual_wait_min FROM prediction_log ORDER BY id"
        ).fetchall()
    assert rows == [("poi-a", 42)]
    assert _sha(source) == before


def test_prediction_wal_source_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "prediction_feedback.db"
    _source(source)
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    with pytest.raises(RuntimeError, match="WAL sources"):
        prediction_feedback.migrate_prediction_feedback_store(
            source=source, destination=destination, apply=True
        )
    assert not destination.exists()


def test_prediction_destination_must_not_preexist(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "prediction_feedback.db"
    _source(source)
    destination.touch()

    with pytest.raises(FileExistsError):
        prediction_feedback.migrate_prediction_feedback_store(
            source=source, destination=destination, apply=True
        )
