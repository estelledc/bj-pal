"""Generate an offline weather contract artifact without making network calls."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from providers import RecordedWeatherProvider, WeatherRequest
from tools.availability_probe import probe
from tools.types import POI


def canonical_artifact_sha256(payload: dict) -> str:
    canonical_payload = deepcopy(payload)
    canonical_payload.pop("artifact_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_weather_acceptance(*, fixture_path: Path, generated_at: datetime | None = None) -> dict:
    request = WeatherRequest(
        latitude=39.9474,
        longitude=116.4166,
        target_local_time="14:00",
        target_date="2026-07-20",
        timezone_name="Asia/Shanghai",
    )
    snapshot = RecordedWeatherProvider(fixture_path).forecast(request)
    decision_context = snapshot.to_decision_context()
    weather_at_target = snapshot.context_at(request.target_local_time)
    outdoor = _poi("outdoor", "玉渊潭公园", "风景名胜", "公园广场")
    indoor = _poi("indoor", "国贸商城", "购物服务", "商场")
    outdoor_result = probe(
        outdoor,
        target_time=request.target_local_time,
        seed=1,
        enable_closed=False,
        record_prediction_log=False,
        weather_context=weather_at_target,
        weather_shelter="open",
    )
    indoor_result = probe(
        indoor,
        target_time=request.target_local_time,
        seed=1,
        enable_closed=False,
        record_prediction_log=False,
        weather_context=weather_at_target,
        weather_shelter="full_indoor",
    )
    fixture_raw = fixture_path.read_bytes()
    payload = {
        "schema_version": 1,
        "artifact_type": "weather_provider_acceptance",
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "acceptance_level": "offline_contract_only",
        "live_network_used": False,
        "live_provider_accepted": False,
        "fixture": {
            "path": "fixtures/weather/beijing_synthetic.json",
            "classification": "synthetic",
            "sha256": hashlib.sha256(fixture_raw).hexdigest(),
        },
        "request": {
            "latitude": request.latitude,
            "longitude": request.longitude,
            "coordinate_scope": "public_area_anchor",
            "target_local_time": request.target_local_time,
            "target_date": request.target_date,
            "timezone_name": request.timezone_name,
        },
        "provider_contract": {
            "provider": "Open-Meteo",
            "documentation": "https://open-meteo.com/en/docs",
            "terms": "https://open-meteo.com/en/terms",
            "attribution": snapshot.attribution,
            "attribution_url": snapshot.attribution_url,
            "license_url": snapshot.license_url,
            "limitations": [
                "The fixture is synthetic and cannot prove current weather accuracy.",
                "The artifact does not prove live endpoint availability or commercial authorization.",
            ],
        },
        "snapshot": decision_context,
        "decision_checks": {
            "outdoor_status": outdoor_result.status,
            "outdoor_action": outdoor_result.fallback_action,
            "indoor_status": indoor_result.status,
            "indoor_action": indoor_result.fallback_action,
        },
    }
    payload["artifact_sha256"] = canonical_artifact_sha256(payload)
    return payload


def _poi(poi_id: str, name: str, category_lv1: str, category_lv2: str) -> POI:
    return POI(
        id=poi_id,
        name=name,
        category_lv1=category_lv1,
        category_lv2=category_lv2,
        category_lv3=None,
        typecode=None,
        district="北京",
        business_area=None,
        address=None,
        longitude=116.4,
        latitude=39.9,
        rating=4.5,
        avg_price=None,
        open_time=None,
        phone=None,
    )
