"""Fail-closed configuration and abuse guard for the hosted synthetic demo."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, MutableSequence


ASGIApp = Callable[
    [dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]],
    Awaitable[None],
]
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PROVIDER_OR_CONTROL_SECRETS = (
    "ANTHROPIC_API_KEY",
    "BJ_PAL_CONTROL_PRINCIPALS_JSON",
    "BJ_PAL_CONTROL_TOKEN",
    "BJ_PAL_JOB_POSTGRES_DSN",
    "DEEPSEEK_API_KEY",
    "DPSK_API_KEY",
    "LONGCAT_API_KEY",
    "OPEN_METEO_API_KEY",
)


def _positive_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    maximum: int,
) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class PublicDemoSettings:
    requests_per_window: int = 20
    window_seconds: int = 60
    max_concurrent_plans: int = 2
    max_body_bytes: int = 8_192

    def __post_init__(self) -> None:
        bounds = {
            "requests_per_window": (self.requests_per_window, 120),
            "window_seconds": (self.window_seconds, 3_600),
            "max_concurrent_plans": (self.max_concurrent_plans, 16),
            "max_body_bytes": (self.max_body_bytes, 65_536),
        }
        for name, (value, maximum) in bounds.items():
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "PublicDemoSettings":
        values = os.environ if environ is None else environ
        return cls(
            requests_per_window=_positive_int(
                values,
                "BJ_PAL_PUBLIC_DEMO_REQUESTS_PER_WINDOW",
                20,
                maximum=120,
            ),
            window_seconds=_positive_int(
                values,
                "BJ_PAL_PUBLIC_DEMO_WINDOW_SECONDS",
                60,
                maximum=3_600,
            ),
            max_concurrent_plans=_positive_int(
                values,
                "BJ_PAL_PUBLIC_DEMO_MAX_CONCURRENT_PLANS",
                2,
                maximum=16,
            ),
            max_body_bytes=_positive_int(
                values,
                "BJ_PAL_PUBLIC_DEMO_MAX_BODY_BYTES",
                8_192,
                maximum=65_536,
            ),
        )


def validate_public_demo_environment(environ: Mapping[str, str] | None = None) -> None:
    values = os.environ if environ is None else environ
    backend = values.get("BJ_PAL_LLM", "mock").strip().lower()
    if backend != "mock":
        raise RuntimeError("public demo requires BJ_PAL_LLM=mock")
    job_store = values.get("BJ_PAL_JOB_STORE", "sqlite").strip().lower()
    if job_store != "sqlite":
        raise RuntimeError("public demo requires BJ_PAL_JOB_STORE=sqlite")
    configured = sorted(name for name in PROVIDER_OR_CONTROL_SECRETS if values.get(name))
    if configured:
        raise RuntimeError(
            "public demo refuses provider or control-plane credentials: "
            + ", ".join(configured)
        )


class FixedWindowLimiter:
    """A process-local aggregate limiter that does not trust proxy identity headers."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: deque[float] = deque()
        self._lock = threading.Lock()

    def admit(self, now: float) -> tuple[bool, int, int]:
        cutoff = now - self.window_seconds
        with self._lock:
            while self._attempts and self._attempts[0] <= cutoff:
                self._attempts.popleft()
            if len(self._attempts) >= self.limit:
                retry_after = max(
                    1,
                    int(self.window_seconds - (now - self._attempts[0]) + 0.999),
                )
                return False, 0, retry_after
            self._attempts.append(now)
            return True, self.limit - len(self._attempts), 0


class PublicDemoGuardMiddleware:
    """Bound raw plan attempts, body size, and concurrent executions before routing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: PublicDemoSettings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.settings = settings
        self.clock = clock
        self.limiter = FixedWindowLimiter(
            limit=settings.requests_per_window,
            window_seconds=settings.window_seconds,
        )
        self.capacity = threading.BoundedSemaphore(settings.max_concurrent_plans)

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path")
        method = scope.get("method")
        request_id = self._request_id(scope)
        if path != "/v1/plans" or method != "POST":
            await self.app(scope, receive, self._decorate_send(send, request_id=request_id))
            return

        admitted, remaining, retry_after = self.limiter.admit(self.clock())
        if not admitted:
            await self._reject(
                send,
                status_code=429,
                code="public_demo_rate_limited",
                message="The aggregate public demo request limit was reached.",
                request_id=request_id,
                retry_after=retry_after,
                remaining=remaining,
            )
            return

        messages, body_size = await self._read_body(receive)
        if body_size > self.settings.max_body_bytes:
            await self._reject(
                send,
                status_code=413,
                code="public_demo_body_too_large",
                message="The public demo request body exceeds its bounded input limit.",
                request_id=request_id,
                remaining=remaining,
            )
            return

        if not self.capacity.acquire(blocking=False):
            await self._reject(
                send,
                status_code=503,
                code="public_demo_busy",
                message="The public demo has reached its concurrent planning limit.",
                request_id=request_id,
                retry_after=1,
                remaining=remaining,
            )
            return

        message_index = 0

        async def replay_receive() -> dict:
            nonlocal message_index
            if message_index < len(messages):
                message = messages[message_index]
                message_index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        try:
            await self.app(
                scope,
                replay_receive,
                self._decorate_send(
                    send,
                    request_id=request_id,
                    remaining=remaining,
                ),
            )
        finally:
            self.capacity.release()

    async def _read_body(self, receive) -> tuple[list[dict], int]:
        messages: list[dict] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") != "http.request":
                break
            total += len(message.get("body", b""))
            if total > self.settings.max_body_bytes or not message.get("more_body", False):
                break
        return messages, total

    @staticmethod
    def _request_id(scope: dict) -> str:
        for raw_name, raw_value in scope.get("headers", ()):
            if raw_name.lower() == b"x-request-id":
                supplied = raw_value.decode("latin-1")
                if REQUEST_ID_PATTERN.fullmatch(supplied):
                    return supplied
        return f"req-{uuid.uuid4().hex}"

    def _decorate_send(
        self,
        send,
        *,
        request_id: str,
        remaining: int | None = None,
    ):
        async def decorated(message: dict) -> None:
            if message.get("type") == "http.response.start":
                managed_names = {
                    b"cache-control",
                    b"referrer-policy",
                    b"x-bj-pal-demo-mode",
                    b"x-content-type-options",
                    b"x-ratelimit-limit",
                    b"x-ratelimit-remaining",
                    b"x-ratelimit-window",
                    b"x-request-id",
                }
                headers: MutableSequence[tuple[bytes, bytes]] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in managed_names
                ]
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-bj-pal-demo-mode", b"synthetic-mock"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-request-id", request_id.encode("ascii")),
                    ]
                )
                if remaining is not None:
                    headers.extend(
                        [
                            (b"x-ratelimit-limit", str(self.settings.requests_per_window).encode()),
                            (b"x-ratelimit-remaining", str(remaining).encode()),
                            (b"x-ratelimit-window", str(self.settings.window_seconds).encode()),
                        ]
                    )
                message["headers"] = headers
            await send(message)

        return decorated

    async def _reject(
        self,
        send,
        *,
        status_code: int,
        code: str,
        message: str,
        request_id: str,
        remaining: int,
        retry_after: int | None = None,
    ) -> None:
        payload = json.dumps(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request_id,
                }
            },
            separators=(",", ":"),
        ).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"cache-control", b"no-store"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-bj-pal-demo-mode", b"synthetic-mock"),
            (b"x-content-type-options", b"nosniff"),
            (b"x-request-id", request_id.encode("ascii")),
            (b"x-ratelimit-limit", str(self.settings.requests_per_window).encode()),
            (b"x-ratelimit-remaining", str(remaining).encode()),
            (b"x-ratelimit-window", str(self.settings.window_seconds).encode()),
        ]
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode()))
        if status_code == 413:
            headers.append((b"connection", b"close"))
        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
