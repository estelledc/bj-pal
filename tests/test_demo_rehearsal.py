"""Keep the default resume/demo path safe, visible, and reproducible."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from demo_cli import _execute_with_clarification, main  # noqa: E402
from operations import (  # noqa: E402
    DEMO_APPROVER_ID,
    OperationSelfApprovalForbidden,
    SideEffectOperationRepository,
    SideEffectOperationService,
    approve_sandbox_booking,
    build_sandbox_booking_draft,
    execute_next_sandbox_booking,
    request_sandbox_booking,
)


def test_default_demo_rehearses_request_approval_worker_and_receipt(
    tmp_path: Path,
) -> None:
    service = SideEffectOperationService(
        repository=SideEffectOperationRepository(tmp_path / "demo-operations.db")
    )
    draft = build_sandbox_booking_draft(
        session_id="test-demo-session",
        poi_id="demo-poi",
        poi_name="演示餐厅",
        target_time="15:00",
        party_size=3,
        amount_minor=12_000,
    )
    requested = request_sandbox_booking(service, draft)
    reused = request_sandbox_booking(service, draft)

    assert requested.status == "pending_approval"
    assert reused.operation_id == requested.operation_id
    assert requested.quote.sandbox is True
    assert requested.action_payload["contact_reference"].startswith("demo-contact-")
    with pytest.raises(OperationSelfApprovalForbidden):
        service.approve(
            operation_id=requested.operation_id,
            tenant_id=requested.tenant_id,
            approved_by=requested.requested_by,
            expected_approval_sha256=requested.approval_sha256,
        )

    approved = approve_sandbox_booking(service, requested)
    executed = execute_next_sandbox_booking(service)

    assert approved.status == "approved"
    assert approved.approved_by == DEMO_APPROVER_ID
    assert executed is not None
    assert executed.operation_id == requested.operation_id
    assert executed.status == "succeeded"
    assert executed.receipt_payload is not None
    assert executed.receipt_payload["sandbox"] is True
    assert [event.event_type for event in service.events(requested.operation_id)] == [
        "requested",
        "request_reused",
        "approved",
        "execution_started",
        "execution_succeeded",
    ]


def test_demo_cli_does_not_bypass_operation_safety_chain() -> None:
    source = inspect.getsource(main)

    assert "request_sandbox_booking" in source
    assert "approve_sandbox_booking" in source
    assert "execute_next_sandbox_booking" in source
    assert "book_restaurant" not in source
    assert "book_cake_delivery" not in source
    assert "send_via_wechat_mock" not in source
    assert "--approve-sandbox-booking" in source


def test_draft_rejects_invalid_party_size() -> None:
    with pytest.raises(ValueError, match="party_size"):
        build_sandbox_booking_draft(
            session_id="test-demo-session",
            poi_id="demo-poi",
            poi_name="演示餐厅",
            target_time="15:00",
            party_size=0,
            amount_minor=12_000,
        )


def test_demo_cli_exposes_durable_clarification_path() -> None:
    source = inspect.getsource(_execute_with_clarification)

    assert "continuation_service.issue" in source
    assert "resolve_request" in source
    assert "claim_execution" in source
    assert "continuation_service.complete" in source
