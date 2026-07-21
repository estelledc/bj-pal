"""Explicit durable-job store selection from process configuration."""

from __future__ import annotations

import os
from typing import Mapping

from .ports import PlanningJobStore
from .repository import PlanningJobRepository


JOB_STORE_ENV = "BJ_PAL_JOB_STORE"
POSTGRES_DSN_ENV = "BJ_PAL_JOB_POSTGRES_DSN"
POSTGRES_SCHEMA_ENV = "BJ_PAL_JOB_POSTGRES_SCHEMA"
POSTGRES_POOL_MIN_SIZE_ENV = "BJ_PAL_JOB_POSTGRES_POOL_MIN_SIZE"
POSTGRES_POOL_MAX_SIZE_ENV = "BJ_PAL_JOB_POSTGRES_POOL_MAX_SIZE"
POSTGRES_POOL_TIMEOUT_SECONDS_ENV = "BJ_PAL_JOB_POSTGRES_POOL_TIMEOUT_SECONDS"
POSTGRES_POOL_MAX_WAITING_ENV = "BJ_PAL_JOB_POSTGRES_POOL_MAX_WAITING"


def _integer_setting(
    values: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = values.get(name, str(default)).strip()
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _float_setting(
    values: Mapping[str, str],
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = values.get(name, str(default)).strip()
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if not minimum <= parsed <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return parsed


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
        from .postgres_repository import (
            DEFAULT_POSTGRES_POOL_MAX_SIZE,
            DEFAULT_POSTGRES_POOL_MAX_WAITING,
            DEFAULT_POSTGRES_POOL_MIN_SIZE,
            DEFAULT_POSTGRES_POOL_TIMEOUT_SECONDS,
            MAX_POSTGRES_POOL_SIZE,
            MAX_POSTGRES_POOL_WAITING,
            PostgresPlanningJobRepository,
        )

        schema = values.get(POSTGRES_SCHEMA_ENV, "public").strip() or "public"
        pool_min_size = _integer_setting(
            values,
            POSTGRES_POOL_MIN_SIZE_ENV,
            default=DEFAULT_POSTGRES_POOL_MIN_SIZE,
            minimum=0,
            maximum=MAX_POSTGRES_POOL_SIZE,
        )
        pool_max_size = _integer_setting(
            values,
            POSTGRES_POOL_MAX_SIZE_ENV,
            default=DEFAULT_POSTGRES_POOL_MAX_SIZE,
            minimum=1,
            maximum=MAX_POSTGRES_POOL_SIZE,
        )
        if pool_min_size > pool_max_size:
            raise RuntimeError(
                f"{POSTGRES_POOL_MIN_SIZE_ENV} must not exceed "
                f"{POSTGRES_POOL_MAX_SIZE_ENV}"
            )
        pool_timeout_seconds = _float_setting(
            values,
            POSTGRES_POOL_TIMEOUT_SECONDS_ENV,
            default=DEFAULT_POSTGRES_POOL_TIMEOUT_SECONDS,
            minimum=0.05,
            maximum=60.0,
        )
        pool_max_waiting = _integer_setting(
            values,
            POSTGRES_POOL_MAX_WAITING_ENV,
            default=DEFAULT_POSTGRES_POOL_MAX_WAITING,
            minimum=1,
            maximum=MAX_POSTGRES_POOL_WAITING,
        )
        return PostgresPlanningJobRepository(
            dsn,
            schema=schema,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
            pool_timeout_seconds=pool_timeout_seconds,
            pool_max_waiting=pool_max_waiting,
        )
    raise RuntimeError(f"unsupported durable job store: {backend or '<empty>'}")
