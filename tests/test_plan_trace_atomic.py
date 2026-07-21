from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import plan_tracer
from agents.plan_tracer import StepTraceInput, iter_steps, replace_steps


@pytest.fixture(autouse=True)
def isolated_plan_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_tracer, "_DB_PATH", tmp_path / "plan-evidence.db")
    plan_tracer._ensure_schema()


def _plan_id() -> str:
    return f"atomic-{uuid.uuid4().hex}"


def test_replace_steps_replaces_a_complete_plan() -> None:
    plan_id = _plan_id()
    replace_steps(
        plan_id,
        [
            StepTraceInput(step_index=1, decision="first", confidence=0.5),
            StepTraceInput(step_index=2, decision="second", confidence=0.6),
        ],
    )
    replace_steps(
        plan_id,
        [StepTraceInput(step_index=1, decision="replacement", confidence=0.8)],
    )
    rows = iter_steps(plan_id)
    assert [(row.step_index, row.decision) for row in rows] == [(1, "replacement")]


def test_replace_steps_validates_every_row_before_deleting_old_trace() -> None:
    plan_id = _plan_id()
    replace_steps(
        plan_id,
        [StepTraceInput(step_index=1, decision="preserved", confidence=0.7)],
    )
    with pytest.raises(ValueError, match="confidence"):
        replace_steps(
            plan_id,
            [StepTraceInput(step_index=1, decision="invalid", confidence=1.5)],
        )
    assert [row.decision for row in iter_steps(plan_id)] == ["preserved"]


def test_replace_steps_rolls_back_delete_when_insert_fails(monkeypatch) -> None:
    plan_id = _plan_id()
    replace_steps(
        plan_id,
        [StepTraceInput(step_index=1, decision="preserved", confidence=0.7)],
    )

    real_conn = plan_tracer._conn

    class FailingConnection:
        def __init__(self):
            self.connection = real_conn()

        def execute(self, sql, parameters=()):
            return self.connection.execute(sql, parameters)

        def executemany(self, sql, parameters):
            raise sqlite3.OperationalError("forced insert failure")

        def close(self):
            self.connection.close()

    monkeypatch.setattr(plan_tracer, "_conn", FailingConnection)
    with pytest.raises(sqlite3.OperationalError, match="forced insert failure"):
        replace_steps(
            plan_id,
            [StepTraceInput(step_index=1, decision="replacement", confidence=0.8)],
        )
    monkeypatch.setattr(plan_tracer, "_conn", real_conn)
    assert [row.decision for row in iter_steps(plan_id)] == ["preserved"]
