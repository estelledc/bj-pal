"""Shared HTTP response helpers with no domain-service dependencies."""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

from .schemas import ErrorResponse


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    payload = ErrorResponse.model_validate(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id(request),
                "details": details,
            }
        }
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
    )
