from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import calibration_history, plan_tracer  # noqa: E402


def _point_modules_at(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(plan_tracer, "_DB_PATH", path)
    monkeypatch.setattr(calibration_history, "_DB_PATH", path)


def test_legacy_outcomes_are_classified_and_human_ui_queries_exclude_synthetic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "trace.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE plan_outcome (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                actual_success INTEGER NOT NULL,
                notes TEXT,
                recorded_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO plan_outcome(plan_id, step_index, actual_success, notes, recorded_at) "
            "VALUES ('legacy-plan', 0, 1, '', 1.0)"
        )
    _point_modules_at(monkeypatch, path)
    plan_tracer._ensure_schema()

    plan_tracer.record_step("synthetic-plan", 0, "synthetic", 0.7)
    plan_tracer.record_outcome("synthetic-plan", 0, True)
    plan_tracer.record_step("human-plan", 0, "human", 0.8)
    plan_tracer.record_outcome(
        "human-plan",
        0,
        True,
        evidence_classification="human_verified_step",
    )

    with sqlite3.connect(path) as connection:
        classifications = dict(
            connection.execute(
                "SELECT plan_id, evidence_classification FROM plan_outcome"
            ).fetchall()
        )
    assert classifications == {
        "legacy-plan": "legacy_unclassified",
        "synthetic-plan": "synthetic_test",
        "human-plan": "human_verified_step",
    }

    human_summary = calibration_history.get_plan_count_summary(
        evidence_classification="human_verified_step"
    )
    human_timeline = calibration_history.get_calibration_timeline(
        window_size=1,
        evidence_classification="human_verified_step",
    )
    assert human_summary["n_outcomes"] == 1
    assert human_summary["n_paired"] == 1
    assert len(human_timeline) == 1
    assert human_timeline[0].mean_actual_success == 1.0


def test_human_step_classification_rejects_free_text_notes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "trace.db"
    _point_modules_at(monkeypatch, path)
    plan_tracer._ensure_schema()

    with pytest.raises(ValueError, match="must not store free-text"):
        plan_tracer.record_outcome(
            "human-plan",
            0,
            True,
            notes="call me later",
            evidence_classification="human_verified_step",
        )
