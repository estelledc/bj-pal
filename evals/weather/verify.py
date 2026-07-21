"""Independent semantic verification for the weather acceptance artifact."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from providers import RecordedWeatherProvider, WeatherRequest


def verify_weather_acceptance(artifact_path: Path, fixture_path: Path) -> dict:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("schema_version") != 1:
        raise ValueError("unsupported weather acceptance schema")
    if artifact.get("artifact_type") != "weather_provider_acceptance":
        raise ValueError("unexpected weather artifact type")
    if artifact.get("artifact_sha256") != _canonical_artifact_sha256(artifact):
        raise ValueError("weather artifact SHA-256 mismatch")
    if artifact.get("acceptance_level") != "offline_contract_only":
        raise ValueError("offline artifact must not claim a higher acceptance level")
    if artifact.get("live_network_used") is not False:
        raise ValueError("offline weather artifact must not claim network access")
    if artifact.get("live_provider_accepted") is not False:
        raise ValueError("offline weather artifact must not claim live acceptance")

    recorded_fixture = artifact.get("fixture") or {}
    fixture_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    if recorded_fixture.get("sha256") != fixture_sha:
        raise ValueError("weather fixture SHA-256 mismatch")
    if recorded_fixture.get("classification") != "synthetic":
        raise ValueError("weather fixture must be labeled synthetic")

    request_payload = artifact.get("request") or {}
    if request_payload.get("coordinate_scope") != "public_area_anchor":
        raise ValueError("weather acceptance must use a public area anchor")
    request = WeatherRequest(
        latitude=float(request_payload["latitude"]),
        longitude=float(request_payload["longitude"]),
        target_local_time=str(request_payload["target_local_time"]),
        target_date=str(request_payload["target_date"]),
        timezone_name=str(request_payload["timezone_name"]),
    )
    expected_snapshot = RecordedWeatherProvider(fixture_path).forecast(request)
    if artifact.get("snapshot") != expected_snapshot.to_decision_context():
        raise ValueError("weather snapshot does not match the fixture contract")

    contract = artifact.get("provider_contract") or {}
    if contract.get("attribution") != expected_snapshot.attribution:
        raise ValueError("weather attribution is missing or changed")
    if contract.get("license_url") != expected_snapshot.license_url:
        raise ValueError("weather license reference is missing or changed")
    serialized_request = json.dumps(request_payload, sort_keys=True).lower()
    if any(secret in serialized_request for secret in ("apikey", "api_key", "token", "secret")):
        raise ValueError("weather acceptance request contains a secret-shaped field")

    decisions = artifact.get("decision_checks") or {}
    if decisions != {
        "outdoor_status": "weather_block",
        "outdoor_action": "reroute",
        "indoor_status": "ok",
        "indoor_action": "proceed",
    }:
        raise ValueError("weather decision checks do not preserve indoor/outdoor behavior")
    return artifact


def _canonical_artifact_sha256(payload: dict) -> str:
    canonical_payload = deepcopy(payload)
    canonical_payload.pop("artifact_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
