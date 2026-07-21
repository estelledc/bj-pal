from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.postgres_pool import (  # noqa: E402
    PostgresPoolArtifactError,
    seal_postgres_pool_artifact,
    verify_postgres_pool_artifact,
)


ARTIFACT = ROOT / "evals" / "results" / "postgres-pool-acceptance.json"


def test_checked_in_postgres_pool_acceptance_is_independently_verifiable() -> None:
    summary = verify_postgres_pool_artifact(
        json.loads(ARTIFACT.read_text(encoding="utf-8"))
    )
    assert summary["gate_pass"] is True
    assert summary["total_operations"] >= 20


def test_postgres_pool_verifier_rejects_tampered_capacity_claim() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(artifact)
    tampered["backpressure"]["held_connections"] = 99
    seal_postgres_pool_artifact(tampered)
    with pytest.raises(PostgresPoolArtifactError, match="configured maximum"):
        verify_postgres_pool_artifact(tampered)
