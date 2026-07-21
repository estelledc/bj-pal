"""Typed audit record produced only by the clarification continuation service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


CLARIFICATION_RESOLUTION_VERSION = "clarification_resolution_v1"
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ClarificationResolution:
    code: str
    field: str
    value: Any
    option_id: str
    answer: str
    decision_sha256: str
    version: str = CLARIFICATION_RESOLUTION_VERSION

    def __post_init__(self) -> None:
        if self.version != CLARIFICATION_RESOLUTION_VERSION:
            raise ValueError("unsupported clarification resolution version")
        if not _SAFE_ID.fullmatch(self.code):
            raise ValueError("clarification resolution code is invalid")
        if not self.field or len(self.field) > 128:
            raise ValueError("clarification resolution field is invalid")
        if not _SAFE_ID.fullmatch(self.option_id):
            raise ValueError("clarification resolution option_id is invalid")
        normalized_answer = self.answer.strip()
        if not normalized_answer or len(normalized_answer) > 2_000:
            raise ValueError("clarification resolution answer must contain 1-2000 characters")
        if not _SHA256.fullmatch(self.decision_sha256):
            raise ValueError("clarification resolution decision_sha256 is invalid")
        if not _is_json_value(self.value):
            raise ValueError("clarification resolution value must be JSON-serializable")
        object.__setattr__(self, "answer", normalized_answer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "code": self.code,
            "field": self.field,
            "value": self.value,
            "option_id": self.option_id,
            "answer": self.answer,
            "decision_sha256": self.decision_sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClarificationResolution":
        if not isinstance(payload, dict):
            raise ValueError("clarification resolution must be an object")
        return cls(
            version=str(payload.get("version") or CLARIFICATION_RESOLUTION_VERSION),
            code=str(payload.get("code") or ""),
            field=str(payload.get("field") or ""),
            value=payload.get("value"),
            option_id=str(payload.get("option_id") or ""),
            answer=str(payload.get("answer") or ""),
            decision_sha256=str(payload.get("decision_sha256") or ""),
        )


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
