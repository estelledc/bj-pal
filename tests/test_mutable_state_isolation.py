"""Regression checks for test/operator overrides of legacy mutable state."""

from __future__ import annotations

from pathlib import Path

from agents import user_memory
from tools import prediction_log


def test_user_memory_environment_override(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "memory" / "user-memory.db"
    monkeypatch.setattr(user_memory, "_DB_PATH", None)
    monkeypatch.setenv(user_memory.USER_MEMORY_DB_ENV, str(target))

    assert user_memory.database_path() == target
    user_memory._ensure_schema()
    assert target.is_file()


def test_prediction_environment_override(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "prediction" / "prediction.db"
    monkeypatch.setattr(prediction_log, "LOG_DB", None)
    monkeypatch.setenv(prediction_log.PREDICTION_DB_ENV, str(target))

    assert prediction_log.database_path() == target
    prediction_log.record_prediction("synthetic-poi", "14:00", 10)
    assert target.is_file()
