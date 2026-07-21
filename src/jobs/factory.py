"""Explicit durable-job store selection from process configuration."""

from __future__ import annotations

import os
from typing import Mapping

from .ports import PlanningJobStore
from .repository import PlanningJobRepository


JOB_STORE_ENV = "BJ_PAL_JOB_STORE"
POSTGRES_DSN_ENV = "BJ_PAL_JOB_POSTGRES_DSN"
POSTGRES_SCHEMA_ENV = "BJ_PAL_JOB_POSTGRES_SCHEMA"


def create_planning_job_store(
    environ: Mapping[str, str] | None = None,
) -> PlanningJobStore:
    """Build the configured store and reject ambiguous credential handling."""
    values = os.environ if environ is None else environ
    backend = values.get(JOB_STORE_ENV, "sqlite").strip().lower()
    dsn = values.get(POSTGRES_DSN_ENV, "").strip()
    if backend == "sqlite":
        if dsn:
            raise RuntimeError(
                f"{POSTGRES_DSN_ENV} is configured while {JOB_STORE_ENV}=sqlite"
            )
        return PlanningJobRepository()
    if backend == "postgres":
        if not dsn:
            raise RuntimeError(
                f"{POSTGRES_DSN_ENV} is required when {JOB_STORE_ENV}=postgres"
            )
        from .postgres_repository import PostgresPlanningJobRepository

        schema = values.get(POSTGRES_SCHEMA_ENV, "public").strip() or "public"
        return PostgresPlanningJobRepository(dsn, schema=schema)
    raise RuntimeError(f"unsupported durable job store: {backend or '<empty>'}")
