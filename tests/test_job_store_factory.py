from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jobs.factory import create_planning_job_store  # noqa: E402
from jobs.repository import PlanningJobRepository  # noqa: E402


def test_job_store_factory_keeps_sqlite_as_the_explicit_default(monkeypatch) -> None:
    monkeypatch.delenv("BJ_PAL_JOB_DB", raising=False)
    store = create_planning_job_store({})
    assert isinstance(store, PlanningJobRepository)


def test_job_store_factory_fails_closed_on_ambiguous_or_unknown_configuration() -> None:
    with pytest.raises(RuntimeError, match="configured.*sqlite"):
        create_planning_job_store(
            {
                "BJ_PAL_JOB_STORE": "sqlite",
                "BJ_PAL_JOB_POSTGRES_DSN": "postgresql://not-opened.example/db",
            }
        )
    with pytest.raises(RuntimeError, match="required"):
        create_planning_job_store({"BJ_PAL_JOB_STORE": "postgres"})
    with pytest.raises(RuntimeError, match="unsupported"):
        create_planning_job_store({"BJ_PAL_JOB_STORE": "memory"})


def test_postgres_repository_repr_never_contains_the_dsn() -> None:
    from jobs.postgres_repository import PostgresPlanningJobRepository

    repository = object.__new__(PostgresPlanningJobRepository)
    repository._dsn = "postgresql://user:secret@example.invalid/db"
    repository.schema = "public"
    rendered = repr(repository)
    assert rendered == "PostgresPlanningJobRepository(schema='public')"
    assert "secret" not in rendered
    assert "example.invalid" not in rendered


@pytest.mark.parametrize("schema", ["Public", "bad-name", "1bad", "a" * 64])
def test_postgres_repository_rejects_unsafe_schema_before_connecting(schema: str) -> None:
    from jobs.postgres_repository import PostgresPlanningJobRepository

    with pytest.raises(ValueError, match="safe lowercase"):
        PostgresPlanningJobRepository(
            "postgresql://not-opened.example/db",
            schema=schema,
        )
