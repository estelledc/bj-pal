"""Durability, idempotency, expiry, and fencing of clarification sessions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.types import UserPreferences  # noqa: E402
from application import (  # noqa: E402
    PlanRequest,
    PlanningClarificationRequired,
    PlanningPreflight,
)
from clarifications import (  # noqa: E402
    ClarificationContinuationService,
    ClarificationExpired,
    ClarificationInProgress,
    ClarificationRepository,
    ClarificationResolutionConflict,
    InvalidClarificationTransition,
)


def _constraint_conflict() -> tuple[PlanRequest, PlanningClarificationRequired]:
    request = PlanRequest(
        user_input="下午三点，两个人去三里屯玩三小时",
        preferences=UserPreferences(
            party_size=4,
            target_start="15:00",
            duration_hours=3,
            raw_input="下午三点，两个人去三里屯玩三小时",
        ),
        provided_fields=frozenset(
            {
                "user_input",
                "preferences.party_size",
                "preferences.target_start",
                "preferences.duration_hours",
            }
        ),
    )
    with pytest.raises(PlanningClarificationRequired) as raised:
        PlanningPreflight().normalize(request)
    return request, raised.value


def test_issue_persists_decision_fingerprint_and_typed_options(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    path = tmp_path / "clarifications.db"
    service = ClarificationContinuationService(
        repository=ClarificationRepository(path),
        ttl_seconds=600,
    )

    issued = service.issue(request=request, error=error, delivery="sync")
    restored = ClarificationRepository(path).get(issued.continuation_id)

    assert restored is not None
    assert restored.request_sha256 == issued.request_sha256
    assert len(restored.decision_sha256) == 64
    assert [item.option_id for item in restored.options] == [
        "use_text_value",
        "use_structured_value",
    ]
    assert restored.options[0].value == 2
    assert restored.options[1].value == 4
    assert restored.to_public_dict()["continue_url"].endswith("/plan")


def test_resolution_is_idempotent_but_a_different_answer_conflicts(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    service = ClarificationContinuationService(
        repository=ClarificationRepository(tmp_path / "clarifications.db")
    )
    issued = service.issue(request=request, error=error, delivery="sync")

    first_session, first_request = service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )
    repeated_session, repeated_request = service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )

    assert first_session.status == repeated_session.status == "resolved"
    assert first_request.to_dict() == repeated_request.to_dict()
    normalized = PlanningPreflight().normalize(first_request)
    assert normalized.request.preferences.party_size == 2
    assert normalized.constraints.conflicts == ()
    with pytest.raises(ClarificationResolutionConflict):
        service.resolve_request(
            continuation_id=issued.continuation_id,
            delivery="sync",
            option_id="use_structured_value",
        )


def test_execution_claim_is_fenced_and_released_for_retry(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    service = ClarificationContinuationService(repository=repository)
    issued = service.issue(request=request, error=error, delivery="sync")
    service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )

    claimed = repository.claim_execution(
        continuation_id=issued.continuation_id,
        owner="worker-a",
    )
    assert claimed.status == "executing"
    with pytest.raises(ClarificationInProgress):
        repository.claim_execution(
            continuation_id=issued.continuation_id,
            owner="worker-b",
        )
    with pytest.raises(InvalidClarificationTransition):
        repository.complete(
            continuation_id=issued.continuation_id,
            owner="worker-b",
            result_payload={"ok": True},
        )
    repository.release_execution(
        continuation_id=issued.continuation_id,
        owner="worker-a",
    )
    reclaimed = repository.claim_execution(
        continuation_id=issued.continuation_id,
        owner="worker-b",
    )
    assert reclaimed.execution_owner == "worker-b"
    completed = repository.complete(
        continuation_id=issued.continuation_id,
        owner="worker-b",
        result_payload={"ok": True},
    )
    assert completed.status == "completed"
    assert completed.result_payload == {"ok": True}


def test_expired_session_cannot_be_resolved(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    service = ClarificationContinuationService(repository=repository)
    issued = service.issue(request=request, error=error, delivery="sync")
    with repository._connect() as connection:
        connection.execute(
            "UPDATE clarification_sessions SET expires_at = ? WHERE continuation_id = ?",
            ("2000-01-01T00:00:00.000Z", issued.continuation_id),
        )

    with pytest.raises(ClarificationExpired):
        service.resolve_request(
            continuation_id=issued.continuation_id,
            delivery="sync",
            option_id="use_text_value",
        )
    assert repository.get(issued.continuation_id).status == "expired"


def test_completed_session_expires_and_raw_request_is_purged(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    service = ClarificationContinuationService(repository=repository)
    issued = service.issue(request=request, error=error, delivery="sync")
    service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )
    repository.claim_execution(
        continuation_id=issued.continuation_id,
        owner="worker-a",
    )
    repository.complete(
        continuation_id=issued.continuation_id,
        owner="worker-a",
        result_payload={"ok": True},
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE clarification_sessions SET expires_at = ? WHERE continuation_id = ?",
            ("2000-01-01T00:00:00.000Z", issued.continuation_id),
        )

    expired = repository.get(issued.continuation_id)

    assert expired is not None
    assert expired.status == "expired"
    assert repository.purge_expired(retention_seconds=0) == 1
    assert repository.get(issued.continuation_id) is None


def test_recently_expired_session_respects_diagnostic_retention(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    issued = ClarificationContinuationService(repository=repository).issue(
        request=request,
        error=error,
        delivery="sync",
    )

    assert repository.purge_expired(retention_seconds=86_400) == 0
    assert repository.get(issued.continuation_id) is not None


def test_decision_envelope_tampering_is_rejected(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    repository = ClarificationRepository(tmp_path / "clarifications.db")
    issued = ClarificationContinuationService(repository=repository).issue(
        request=request,
        error=error,
        delivery="sync",
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE clarification_sessions SET options_json = ? WHERE continuation_id = ?",
            ("[]", issued.continuation_id),
        )

    with pytest.raises(ValueError, match="decision SHA-256"):
        repository.get(issued.continuation_id)


def test_resolution_and_result_tampering_are_rejected(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    path = tmp_path / "clarifications.db"
    repository = ClarificationRepository(path)
    service = ClarificationContinuationService(repository=repository)
    issued = service.issue(request=request, error=error, delivery="sync")
    service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE clarification_sessions SET resolution_json = ? WHERE continuation_id = ?",
            ('{"value":999}', issued.continuation_id),
        )
    with pytest.raises(ValueError, match="resolution SHA-256"):
        repository.get(issued.continuation_id)

    second = service.issue(request=request, error=error, delivery="sync")
    service.resolve_request(
        continuation_id=second.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )
    repository.claim_execution(
        continuation_id=second.continuation_id,
        owner="worker-a",
    )
    repository.complete(
        continuation_id=second.continuation_id,
        owner="worker-a",
        result_payload={"ok": True},
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE clarification_sessions SET result_json = ? WHERE continuation_id = ?",
            ('{"ok":false}', second.continuation_id),
        )
    with pytest.raises(ValueError, match="result SHA-256"):
        repository.get(second.continuation_id)


def test_early_v56_rows_backfill_integrity_hashes_on_reopen(tmp_path: Path) -> None:
    request, error = _constraint_conflict()
    path = tmp_path / "clarifications.db"
    repository = ClarificationRepository(path)
    service = ClarificationContinuationService(repository=repository)
    issued = service.issue(request=request, error=error, delivery="sync")
    service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="use_text_value",
    )
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE clarification_sessions
            SET resolution_sha256 = NULL, resolved_request_sha256 = NULL
            WHERE continuation_id = ?
            """,
            (issued.continuation_id,),
        )

    restored = ClarificationRepository(path).get(issued.continuation_id)

    assert restored is not None
    assert restored.resolution_sha256 is not None
    assert restored.resolved_request_sha256 is not None


def test_unresolved_reference_can_restart_without_historical_context(tmp_path: Path) -> None:
    request = PlanRequest(user_input="按之前的方案继续安排")
    with pytest.raises(PlanningClarificationRequired) as raised:
        PlanningPreflight().normalize(request)
    service = ClarificationContinuationService(
        repository=ClarificationRepository(tmp_path / "clarifications.db")
    )
    issued = service.issue(request=request, error=raised.value, delivery="sync")

    _, resolved = service.resolve_request(
        continuation_id=issued.continuation_id,
        delivery="sync",
        option_id="restart_with_area",
    )
    normalized = PlanningPreflight().normalize(resolved)

    assert normalized.request.user_input == "在五道营-雍和宫片区重新生成本次短时活动方案"
    assert normalized.requirements.requires_clarification is False
