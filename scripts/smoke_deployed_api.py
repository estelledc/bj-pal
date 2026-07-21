"""Smoke a running BJ-Pal image without printing plan or credential payloads."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("remote smoke targets must use HTTPS")
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def requires_public_demo_contract(version: str) -> bool:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError("expected version must use canonical MAJOR.MINOR.PATCH format")
    return tuple(int(part) for part in match.groups()) >= (6, 25, 0)


def request_json(
    base_url: str,
    path: str,
    *,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Mapping[str, str]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        urljoin(base_url, path.lstrip("/")),
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Request-ID": f"oci-smoke-{path.strip('/').replace('/', '-')}",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is validated
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise RuntimeError(f"{path} did not return a JSON object")
        return body, {
            name.lower(): value for name, value in response.headers.items()
        }


def smoke(base_url: str, *, expected_version: str, timeout: float) -> dict[str, Any]:
    normalized = normalize_base_url(base_url)
    public_demo_contract = requires_public_demo_contract(expected_version)
    health, health_headers = request_json(normalized, "/healthz", timeout=timeout)
    if health != {"status": "ok", "service": "bj-pal", "version": expected_version}:
        raise RuntimeError("health response did not match the release contract")

    readiness, readiness_headers = request_json(normalized, "/readyz", timeout=timeout)
    if readiness.get("status") != "ready":
        raise RuntimeError("readiness response was not ready")

    schema, schema_headers = request_json(normalized, "/openapi.json", timeout=timeout)
    if schema.get("info", {}).get("version") != expected_version:
        raise RuntimeError("OpenAPI version did not match the release")
    schema_paths = set(schema.get("paths", {}))
    if public_demo_contract:
        expected_paths = {"/healthz", "/readyz", "/v1/plans"}
        if schema_paths != expected_paths:
            raise RuntimeError("OpenAPI schema exposed routes outside the public demo contract")
    elif "/v1/plans" not in schema_paths:
        raise RuntimeError("OpenAPI schema did not expose /v1/plans")

    plan, plan_headers = request_json(
        normalized,
        "/v1/plans",
        timeout=timeout,
        payload={
            "user_input": "周末下午带 5 岁孩子在五道营附近玩四小时，不吃辣",
            "persona": "family",
            "preferences": {
                "party_size": 3,
                "has_child": True,
                "child_age": 5,
                "diet_flags": ["no_spicy"],
                "duration_hours": 4,
            },
        },
    )
    if plan.get("data_profile", {}).get("classification") != "synthetic":
        raise RuntimeError("public image must identify its bundled data as synthetic")
    if not plan.get("final_plan", {}).get("steps"):
        raise RuntimeError("planning smoke returned no final steps")
    if public_demo_contract and plan.get("feedback") is not None:
        raise RuntimeError("public demo must not issue a persisted feedback capability")
    response_headers = (
        health_headers,
        readiness_headers,
        schema_headers,
        plan_headers,
    )
    if any(not headers.get("x-request-id") for headers in response_headers):
        raise RuntimeError("request ID propagation was missing")
    if public_demo_contract:
        if any(
            headers.get("x-bj-pal-demo-mode") != "synthetic-mock"
            for headers in response_headers
        ):
            raise RuntimeError("public demo mode header was missing")
        if plan_headers.get("cache-control") != "no-store":
            raise RuntimeError("public plan responses must not be cached")

    checks = ["health", "readiness"]
    if public_demo_contract:
        checks.extend(
            [
                "public_openapi_allowlist",
                "fixed_synthetic_plan",
                "no_feedback_capability",
                "abuse_guard_headers",
            ]
        )
    else:
        checks.extend(["openapi", "fixed_synthetic_plan"])

    return {
        "version": expected_version,
        "data_classification": "synthetic",
        "checks": checks,
        "request_id_propagation": "present",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    result = smoke(
        args.base_url,
        expected_version=args.expected_version,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
