"""Persistence-neutral continuation models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ClarificationDelivery = Literal["sync", "job"]
ClarificationStatus = Literal[
    "pending",
    "resolved",
    "executing",
    "completed",
    "expired",
]


@dataclass(frozen=True)
class ClarificationOption:
    option_id: str
    label: str
    field: str
    value: Any = None
    requires_answer: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "label": self.label,
            "field": self.field,
            "value": self.value,
            "requires_answer": self.requires_answer,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClarificationOption":
        return cls(
            option_id=str(payload["option_id"]),
            label=str(payload["label"]),
            field=str(payload["field"]),
            value=payload.get("value"),
            requires_answer=bool(payload.get("requires_answer", False)),
        )


@dataclass(frozen=True)
class ClarificationSession:
    continuation_id: str
    delivery: ClarificationDelivery
    status: ClarificationStatus
    request_payload: dict[str, Any]
    request_sha256: str
    decision_payload: dict[str, Any]
    decision_sha256: str
    constraints_payload: dict[str, Any] | None
    job_policy: dict[str, Any]
    options: tuple[ClarificationOption, ...]
    resolution_payload: dict[str, Any] | None
    resolution_sha256: str | None
    resolved_request_payload: dict[str, Any] | None
    resolved_request_sha256: str | None
    result_payload: dict[str, Any] | None
    result_sha256: str | None
    created_at: str
    expires_at: str
    resolved_at: str | None
    completed_at: str | None
    execution_owner: str | None
    execution_lease_expires_at: str | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "version": "clarification_continuation_v1",
            "continuation_id": self.continuation_id,
            "delivery": self.delivery,
            "status": self.status,
            "decision_sha256": self.decision_sha256,
            "expires_at": self.expires_at,
            "options": [item.to_dict() for item in self.options],
            "continue_url": (
                f"/v1/clarifications/{self.continuation_id}/plan"
                if self.delivery == "sync"
                else f"/v1/clarifications/{self.continuation_id}/planning-job"
            ),
        }
