from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import calibration_history, plan_tracer  # noqa: E402
from storage import state_layout  # noqa: E402


def _source(path: Path, *, legacy_outcome_schema: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        if legacy_outcome_schema:
            connection.executescript(
                """
                CREATE TABLE plan_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    step_kind TEXT,
                    poi_id TEXT,
                    decision TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    fallback_action TEXT,
                    evidence TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE plan_outcome (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    actual_success INTEGER NOT NULL,
                    notes TEXT,
                    recorded_at REAL NOT NULL
                );
                """
            )
        else:
            connection.executescript(state_layout.PLAN_EVIDENCE_SCHEMA)
        connection.execute(
            """
            INSERT INTO plan_trace(
                id, plan_id, step_index, step_kind, poi_id, decision,
                confidence, fallback_action, evidence, created_at
            ) VALUES (1, 'plan-1', 0, 'meal', 'poi-1', 'choose fixture',
                      0.75, NULL, '{"source":"fixture"}', 1.0)
            """
        )
        outcome_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(plan_outcome)")
        }
        if "evidence_classification" in outcome_columns:
            connection.execute(
                """
                INSERT INTO plan_outcome(
                    id, plan_id, step_index, actual_success, notes,
                    evidence_classification, recorded_at
                ) VALUES (1, 'plan-1', 0, 1, '', 'synthetic_test', 2.0)
                """
            )
        else:
            connection.execute(
                """
                INSERT INTO plan_outcome(
                    id, plan_id, step_index, actual_success, notes, recorded_at
                ) VALUES (1, 'plan-1', 0, 1, '', 2.0)
                """
            )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_is_read_only_and_does_not_create_destination(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "plan_evidence.db"
    _source(source)
    before = _sha(source)

    result = state_layout.migrate_plan_evidence_store(
        source=source,
        destination=destination,
    )

    assert result["mode"] == "dry_run"
    assert result["source_counts"] == {"plan_trace": 1, "plan_outcome": 1}
    assert result["legacy_source_modified"] is False
    assert _sha(source) == before
    assert not destination.exists()


def test_apply_copies_exact_snapshot_and_preserves_legacy_bytes(tmp_path: Path) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "plan_evidence.db"
    _source(source)
    before = _sha(source)

    result = state_layout.migrate_plan_evidence_store(
        source=source,
        destination=destination,
        apply=True,
    )

    assert result["receipt_valid"] is True
    assert result["destination_quick_check"] == "ok"
    assert result["source_counts"] == result["destination_counts"]
    assert result["source_digests"] == result["destination_digests"]
    assert _sha(source) == before
    assert state_layout.inspect_plan_evidence_store(source)["digests"] == (
        state_layout.inspect_plan_evidence_store(destination)["digests"]
    )
    with sqlite3.connect(destination) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert tables == {"plan_trace", "plan_outcome", "state_store_metadata"}
    assert mode == "delete"
    assert destination.stat().st_mode & 0o777 == 0o600


def test_legacy_outcomes_gain_explicit_classification_during_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    destination = tmp_path / "plan_evidence.db"
    _source(source, legacy_outcome_schema=True)

    state_layout.migrate_plan_evidence_store(
        source=source,
        destination=destination,
        apply=True,
    )

    with sqlite3.connect(destination) as connection:
        classification = connection.execute(
            "SELECT evidence_classification FROM plan_outcome WHERE id=1"
        ).fetchone()[0]
    assert classification == "legacy_unclassified"


def test_resolver_uses_legacy_until_verified_receipt_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "plan_evidence.db"
    _source(source)
    monkeypatch.setattr(state_layout, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(state_layout, "PLAN_EVIDENCE_DEFAULT_DB", destination)
    monkeypatch.delenv(state_layout.PLAN_EVIDENCE_DB_ENV, raising=False)

    assert state_layout.resolve_plan_evidence_path() == source

    state_layout.migrate_plan_evidence_store(
        source=source,
        destination=destination,
        apply=True,
    )

    assert state_layout.resolve_plan_evidence_path() == destination


def test_resolved_new_writes_do_not_mutate_legacy_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "tool_calls.db"
    destination = tmp_path / "runtime" / "plan_evidence.db"
    _source(source)
    state_layout.migrate_plan_evidence_store(
        source=source,
        destination=destination,
        apply=True,
    )
    monkeypatch.setattr(state_layout, "LEGACY_SHARED_DB", source)
    monkeypatch.setattr(state_layout, "PLAN_EVIDENCE_DEFAULT_DB", destination)
    monkeypatch.setattr(plan_tracer, "_DB_PATH", None)
    monkeypatch.setattr(calibration_history, "_DB_PATH", None)
    monkeypatch.delenv(state_layout.PLAN_EVIDENCE_DB_ENV, raising=False)
    legacy_before = _sha(source)

    plan_tracer.record_step("plan-after-migration", 0, "new write", 0.9)

    with sqlite3.connect(source) as connection:
        legacy_count = connection.execute("SELECT COUNT(*) FROM plan_trace").fetchone()[0]
    with sqlite3.connect(destination) as connection:
        destination_count = connection.execute(
            "SELECT COUNT(*) FROM plan_trace"
        ).fetchone()[0]
    assert plan_tracer.database_path() == destination
    assert legacy_count == 1
    assert destination_count == 2
    assert _sha(source) == legacy_before


def test_wal_source_fails_closed_before_destination_creation(tmp_path: Path) -> None:
    source = tmp_path / "wal-source.db"
    destination = tmp_path / "plan_evidence.db"
    _source(source)
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"

    with pytest.raises(RuntimeError, match="WAL sources"):
        state_layout.migrate_plan_evidence_store(
            source=source,
            destination=destination,
            apply=True,
        )

    assert not destination.exists()


def test_trace_and_calibration_share_the_same_migrated_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "plan_evidence.db"
    monkeypatch.setattr(plan_tracer, "_DB_PATH", database)
    monkeypatch.setattr(calibration_history, "_DB_PATH", None)
    plan_tracer._ensure_schema()
    plan_tracer.record_step("plan-new", 0, "fixture", 0.8)
    plan_tracer.record_outcome("plan-new", 0, True)

    summary = calibration_history.get_plan_count_summary()

    assert plan_tracer.database_path() == database
    assert calibration_history.database_path() == database
    assert summary["n_plans"] == 1
    assert summary["n_outcomes"] == 1
    assert summary["n_paired"] == 1
