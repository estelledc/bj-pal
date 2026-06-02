"""Project-level agent skill registry.

These skills are reusable BJ-Pal agent capabilities exposed behind a small,
typed runtime. They are intentionally thin wrappers over existing modules, so
the planner/replanner code remains the source of truth while UI and tests can
discover and invoke stable units.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Optional


SkillHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class AgentSkill:
    """Metadata and callable handler for one reusable agent capability."""

    name: str
    label: str
    description: str
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]
    handler: SkillHandler = field(repr=False, compare=False)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "input_keys": list(self.input_keys),
            "output_keys": list(self.output_keys),
        }


@dataclass
class SkillResult:
    """Standard result envelope returned by every project skill."""

    skill_name: str
    ok: bool
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    error: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "ok": self.ok,
            "summary": self.summary,
            "output": self.output,
            "evidence": list(self.evidence),
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


def list_skills() -> list[AgentSkill]:
    """Return all registered skills in display order."""

    return list(_SKILLS.values())


def describe_skills() -> list[dict[str, Any]]:
    """Return a JSON-serializable skill catalogue for UI/diagnostics."""

    return [skill.describe() for skill in list_skills()]


def get_skill(name: str) -> AgentSkill:
    """Fetch a registered skill by name."""

    try:
        return _SKILLS[name]
    except KeyError:
        raise KeyError(f"Unknown agent skill: {name}") from None


def run_skill(name: str, payload: Optional[dict[str, Any]] = None) -> SkillResult:
    """Invoke a registered skill with a standard result envelope."""

    skill = get_skill(name)
    started = perf_counter()
    try:
        output = dict(skill.handler(dict(payload or {})))
        summary = str(output.pop("_summary", f"{skill.label} 已完成"))
        evidence = [str(v) for v in output.pop("_evidence", [])]
        return SkillResult(
            skill_name=name,
            ok=True,
            summary=summary,
            output=output,
            evidence=evidence,
            latency_ms=_elapsed_ms(started),
        )
    except Exception as exc:
        return SkillResult(
            skill_name=name,
            ok=False,
            summary=f"{skill.label} 执行失败",
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
        )


def _run_preference_intake(payload: dict[str, Any]) -> dict[str, Any]:
    from .text_intake import extract_from_text

    text = str(payload.get("text") or payload.get("raw") or "")
    use_llm = bool(payload.get("use_llm", True))
    client = payload.get("client")
    result = extract_from_text(text, client=client, use_llm=use_llm)
    out = result.to_dict()
    out["_summary"] = _summarize_intake(out)
    out["_evidence"] = [f"source={result.source}", f"text_len={len(text.strip())}"]
    return out


def _run_poi_search(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.amap_search import resolve_area_center, search_pois

    area_anchor = str(payload.get("area_anchor") or "")
    category = str(payload.get("category") or "all")
    limit = _int(payload.get("limit"), default=10, minimum=1, maximum=50)
    constraints = _search_constraints_from_payload(payload)
    pois = search_pois(
        area_anchor=area_anchor or None,
        category=category,  # type: ignore[arg-type]
        constraints=constraints,
        limit=limit,
    )
    center = resolve_area_center(area_anchor) if area_anchor else None
    return {
        "area_anchor": area_anchor,
        "category": category,
        "center": list(center) if center else None,
        "pois": [_poi_to_brief(p) for p in pois],
        "_summary": f"找到 {len(pois)} 个候选 POI",
        "_evidence": [
            f"area_anchor={area_anchor or 'global'}",
            f"category={category}",
            f"limit={limit}",
        ],
    }


def _run_risk_probe(payload: dict[str, Any]) -> dict[str, Any]:
    from tools.availability_probe import probe

    poi = _poi_from_payload(payload)
    party_size = _int(payload.get("party_size"), default=3, minimum=1, maximum=20)
    target_time = str(payload.get("target_time") or "")
    seed = payload.get("seed")
    if seed is not None:
        seed = _int(seed, default=0, minimum=0, maximum=10_000_000)
    result = probe(
        poi,
        party_size=party_size,
        target_time=target_time or None,
        seed=seed,
        enable_weather=bool(payload.get("enable_weather", True)),
        enable_closed=bool(payload.get("enable_closed", True)),
        record_prediction_log=bool(payload.get("record_prediction_log", False)),
        driving=bool(payload.get("driving", False)),
    )
    out = result.to_dict()
    out["reason"] = result.reason
    out["_summary"] = (
        f"{poi.name}: {result.status}, 等待 {result.wait_min} 分钟"
        if result.wait_min
        else f"{poi.name}: {result.status}"
    )
    out["_evidence"] = list(result.evidence[:3])
    return out


def _run_candidate_screening(payload: dict[str, Any]) -> dict[str, Any]:
    from .planner import screen_candidates

    prefs = _user_preferences_from_payload(payload)
    result = screen_candidates(
        user_input=str(payload.get("user_input") or payload.get("text") or ""),
        persona=str(payload.get("persona") or prefs.persona),
        prefs=prefs,
        area_anchor=str(payload.get("area_anchor") or "五道营-雍和宫片区"),
        category=str(payload.get("category") or "food"),
        top_k=_int(payload.get("top_k"), default=8, minimum=1, maximum=20),
    )
    candidates = result.get("candidates") or []
    return {
        **result,
        "_summary": f"筛出 {len(candidates)} 个候选",
        "_evidence": [
            f"mode={result.get('mode', 'screening')}",
            f"area_anchor={result.get('area_anchor', '')}",
            f"category={result.get('category', '')}",
        ],
    }


def _run_weekend_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from .llm_client import MockLLMClient
    from .planner import plan

    prefs = _user_preferences_from_payload(payload)
    client = MockLLMClient() if payload.get("use_mock_client") else payload.get("client")
    generated = plan(
        user_input=str(payload.get("user_input") or payload.get("text") or prefs.raw_input),
        persona=str(payload.get("persona") or prefs.persona),
        prefs=prefs,
        area_anchor=str(payload.get("area_anchor") or "五道营-雍和宫片区"),
        client=client,
        branch_hint=str(payload.get("branch_hint") or ""),
        temperature=float(payload.get("temperature", 0.3)),
        user_id=payload.get("user_id"),
    )
    return {
        "plan": generated.to_dict(),
        "_summary": f"生成 {len(generated.steps)} 步周末闲时路线",
        "_evidence": [
            f"plan_id={generated.plan_id}",
            f"area_anchor={generated.area_anchor}",
        ],
    }


def _user_preferences_from_payload(payload: dict[str, Any]):
    from .types import UserPreferences

    data = dict(payload.get("prefs") or {})
    return UserPreferences(
        persona=str(data.get("persona") or payload.get("persona") or "family"),
        party_size=_int(data.get("party_size", payload.get("party_size")),
                        default=3, minimum=1, maximum=20),
        has_child=bool(data.get("has_child", payload.get("has_child", False))),
        child_age=_optional_int(data.get("child_age", payload.get("child_age"))),
        diet_flags=list(data.get("diet_flags", payload.get("diet_flags", [])) or []),
        walk_radius_km=float(data.get("walk_radius_km", payload.get("walk_radius_km", 1.5))),
        budget_per_person=_optional_float(
            data.get("budget_per_person", payload.get("budget_per_person"))
        ),
        target_start=str(data.get("target_start", payload.get("target_start", "14:00"))),
        duration_hours=float(data.get("duration_hours", payload.get("duration_hours", 4.5))),
        raw_input=str(data.get("raw_input") or payload.get("user_input") or payload.get("text") or ""),
    )


def _search_constraints_from_payload(payload: dict[str, Any]):
    from tools.types import SearchConstraints

    data = dict(payload.get("constraints") or {})
    return SearchConstraints(
        persona=str(data.get("persona") or payload.get("persona") or "family"),
        party_size=_int(data.get("party_size", payload.get("party_size")),
                        default=3, minimum=1, maximum=20),
        has_child=bool(data.get("has_child", payload.get("has_child", False))),
        child_age=_optional_int(data.get("child_age", payload.get("child_age"))),
        diet_flags=list(data.get("diet_flags", payload.get("diet_flags", [])) or []),
        walk_radius_km=float(data.get("walk_radius_km", payload.get("walk_radius_km", 1.5))),
        budget_per_person=_optional_float(
            data.get("budget_per_person", payload.get("budget_per_person"))
        ),
        open_at=data.get("open_at", payload.get("open_at")),
        min_rating=float(data.get("min_rating", payload.get("min_rating", 4.0))),
    )


def _poi_from_payload(payload: dict[str, Any]):
    from tools.types import POI

    data = dict(payload.get("poi") or payload)
    name = str(data.get("name") or data.get("poi_name") or "")
    if not name:
        raise ValueError("risk_probe requires poi.name or poi_name")
    return POI(
        id=str(data.get("id") or data.get("poi_id") or name),
        name=name,
        category_lv1=data.get("category_lv1"),
        category_lv2=data.get("category_lv2"),
        category_lv3=data.get("category_lv3"),
        typecode=data.get("typecode"),
        district=data.get("district"),
        business_area=data.get("business_area"),
        address=data.get("address"),
        longitude=_optional_float(data.get("longitude")),
        latitude=_optional_float(data.get("latitude")),
        rating=_optional_float(data.get("rating")),
        avg_price=_optional_float(data.get("avg_price")),
        open_time=data.get("open_time"),
        phone=data.get("phone"),
        photos=list(data.get("photos") or []),
    )


def _poi_to_brief(poi) -> dict[str, Any]:
    return {
        "id": poi.id,
        "name": poi.name,
        "category_lv1": poi.category_lv1,
        "category_lv2": poi.category_lv2,
        "district": poi.district,
        "business_area": poi.business_area,
        "address": poi.address,
        "longitude": poi.longitude,
        "latitude": poi.latitude,
        "rating": poi.rating,
        "avg_price": poi.avg_price,
        "open_time": poi.open_time,
    }


def _summarize_intake(output: dict[str, Any]) -> str:
    bits = []
    if output.get("area_anchor"):
        bits.append(str(output["area_anchor"]))
    if output.get("poi_name"):
        bits.append(str(output["poi_name"]))
    tag_count = (
        len(output.get("taste_tags") or [])
        + len(output.get("scene_tags") or [])
        + len(output.get("risk_tags") or [])
    )
    if tag_count:
        bits.append(f"{tag_count} 个偏好/风险标签")
    return "抽取到 " + " / ".join(bits) if bits else "未抽取到明确偏好"


def _int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 2)


_SKILLS: dict[str, AgentSkill] = {
    "preference_intake": AgentSkill(
        name="preference_intake",
        label="偏好抽取",
        description="从用户粘贴文本、截图 OCR 文本或口头偏好中抽取片区、口味、场景和风险标签。",
        input_keys=("text", "use_llm", "client"),
        output_keys=("area_anchor", "poi_name", "taste_tags", "scene_tags", "risk_tags", "aspects"),
        handler=_run_preference_intake,
    ),
    "poi_search": AgentSkill(
        name="poi_search",
        label="POI 检索",
        description="按片区、类目、预算、评分和步行半径检索真实 POI 候选。",
        input_keys=("area_anchor", "category", "limit", "constraints"),
        output_keys=("area_anchor", "category", "center", "pois"),
        handler=_run_poi_search,
    ),
    "risk_probe": AgentSkill(
        name="risk_probe",
        label="风险探针",
        description="对单个 POI 做排队、天气、闭店、预约等可达性探测，并返回 reroute 触发信号。",
        input_keys=("poi", "party_size", "target_time", "seed", "enable_weather", "enable_closed"),
        output_keys=("status", "wait_min", "fallback_action", "reason", "evidence", "risk_tags"),
        handler=_run_risk_probe,
    ),
    "candidate_screening": AgentSkill(
        name="candidate_screening",
        label="候选筛选",
        description="重要饭局或不需要完整规划时，只输出带理由和风险的候选 POI shortlist。",
        input_keys=("user_input", "persona", "prefs", "area_anchor", "category", "top_k"),
        output_keys=("mode", "area_anchor", "category", "candidates", "decision_hint"),
        handler=_run_candidate_screening,
    ),
    "weekend_plan": AgentSkill(
        name="weekend_plan",
        label="周末路线生成",
        description="复用 planner 生成 3-5 小时周末闲时路线；默认走当前配置的 LLM 后端，可显式注入 mock client。",
        input_keys=("user_input", "persona", "prefs", "area_anchor", "client", "use_mock_client"),
        output_keys=("plan",),
        handler=_run_weekend_plan,
    ),
}
