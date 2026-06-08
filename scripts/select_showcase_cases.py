"""Run many planner scenarios and select doc-ready showcase cases.

The existing 100-case LongCat harness is good for aggregate metrics. This script
adds a presentation layer: run a sizeable subset, score each result for
document usefulness, then write the 4-10 strongest examples as JSON/Markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.run_longcat_demo import SCENARIOS, run_one  # noqa: E402


DEFAULT_RAW_OUT = ROOT / "data" / "showcase_candidates_longcat.json"
DEFAULT_JSON_OUT = ROOT / "docs" / "showcase_test_cases.json"
DEFAULT_MD_OUT = ROOT / "docs" / "showcase_test_cases.md"

SELECTION_CRITERIA = [
    "端到端成功交付，且最终方案保留 4-8 个清晰步骤",
    "POI 不重复，步骤时间、类型、理由适合直接展示",
    "能体现用户画像、偏好、禁忌、预算、亲子/父母/朋友等差异化需求",
    "优先保留发生 reroute 或风险处理的案例，展示 agent 可调整能力",
    "最终 4-10 条尽量覆盖不同 persona、片区和任务类型",
]

PERSONA_ZH = {
    "family": "亲子/家庭",
    "friends": "朋友聚会",
    "solo": "独自出行",
    "with_parents": "陪父母",
}


def run_candidates(
    *,
    backend: str,
    limit: int,
    skip: int = 0,
    scenario_ids: list[str] | None = None,
    raw_out: Path = DEFAULT_RAW_OUT,
    resume: bool = True,
) -> list[dict[str, Any]]:
    """Run planner scenarios and persist after every case."""
    os.environ["BJ_PAL_LLM"] = backend
    raw_out.parent.mkdir(parents=True, exist_ok=True)

    scenarios = list(SCENARIOS)
    if scenario_ids:
        wanted = set(scenario_ids)
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if skip:
        scenarios = scenarios[skip:]
    if limit:
        scenarios = scenarios[:limit]

    results: list[dict[str, Any]] = []
    done: set[str] = set()
    if resume and raw_out.exists():
        try:
            existing = json.loads(raw_out.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                results = existing
                done = {r.get("scenario", "") for r in results if r.get("scenario")}
        except Exception:
            results = []
            done = set()

    todo = [s for s in scenarios if s["id"] not in done]
    print(
        f"backend={backend} candidates={len(scenarios)} "
        f"already_recorded={len(done)} todo={len(todo)}",
        flush=True,
    )

    t0 = time.time()
    for i, scenario in enumerate(todo, start=1):
        print(
            f"\n=== [{i}/{len(todo)}] {scenario['id']} "
            f"{scenario['title']} ===",
            flush=True,
        )
        results.append(run_one(scenario))
        raw_out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        elapsed = time.time() - t0
        avg = elapsed / max(1, i)
        print(
            f"saved={raw_out} elapsed={elapsed:.0f}s avg={avg:.1f}s/case",
            flush=True,
        )
    return results


def build_showcase_report(
    results: list[dict[str, Any]],
    *,
    selected_count: int,
    backend: str,
    source_path: str,
    excluded_scenarios: list[str] | None = None,
) -> dict[str, Any]:
    excluded = set(excluded_scenarios or [])
    scored = []
    for result in results:
        score, reasons, metrics = calculate_showcase_score(result)
        scored.append({
            "result": result,
            "score": score,
            "reasons": reasons,
            "metrics": metrics,
        })

    selectable = [
        item for item in scored
        if item["result"].get("scenario") not in excluded
    ]
    selected = select_showcases(selectable, selected_count=selected_count)
    selected_scenarios = {item["result"].get("scenario") for item in selected}
    selected_cases = [
        case_to_showcase(item["result"], item["score"], item["reasons"], item["metrics"], rank=i)
        for i, item in enumerate(selected, start=1)
    ]
    summaries = [
        {
            "scenario": item["result"].get("scenario"),
            "title": item["result"].get("title"),
            "persona": _input(item["result"]).get("persona"),
            "ok": bool(item["result"].get("ok")),
            "score": round(item["score"], 2),
            "selected": item["result"].get("scenario") in selected_scenarios,
            "excluded_from_selection": item["result"].get("scenario") in excluded,
            "top_reasons": item["reasons"][:3],
            "error": item["result"].get("error"),
        }
        for item in sorted(scored, key=lambda x: x["score"], reverse=True)
    ]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "backend": backend,
        "source_path": source_path,
        "total_candidates_run": len(results),
        "selected_count": len(selected_cases),
        "excluded_scenarios": sorted(excluded),
        "selection_criteria": list(SELECTION_CRITERIA),
        "selected_cases": selected_cases,
        "all_case_summaries": summaries,
    }


def calculate_showcase_score(result: dict[str, Any]) -> tuple[float, list[str], dict[str, Any]]:
    """Return a presentation score, human-readable reasons, and metrics."""
    if not result.get("ok"):
        return -100.0, ["未成功交付，不能作为展示案例"], {
            "showcase_score": -100.0,
            "ok": False,
            "step_count": 0,
            "unique_poi_count": 0,
            "reroute_count": 0,
        }

    plan = _plan(result)
    steps = plan.get("steps") or []
    real_steps = [s for s in steps if s.get("kind") != "depart" and s.get("poi_name")]
    poi_names = [s.get("poi_name") for s in real_steps if s.get("poi_name")]
    unique_pois = len(set(poi_names))
    duplicate_pois = max(0, len(poi_names) - unique_pois)
    rationale_count = sum(1 for s in real_steps if len((s.get("rationale") or "").strip()) >= 12)
    events = result.get("events") or []
    prefs = _input(result).get("prefs") or {}
    persona = _input(result).get("persona") or prefs.get("persona") or ""
    user_input = _input(result).get("user_input") or ""
    diet_flags = prefs.get("diet_flags") or []

    score = 0.0
    reasons: list[str] = []

    score += 8.0
    reasons.append("端到端成功返回规划结果")

    if 4 <= len(steps) <= 8:
        score += 4.0
        reasons.append(f"步骤数 {len(steps)}，适合文档展示")
    else:
        score -= abs(len(steps) - 5) * 1.5

    if duplicate_pois == 0 and unique_pois >= 3:
        score += 4.0
        reasons.append("POI 无重复，路线结构清楚")
    else:
        score -= duplicate_pois * 3.0
        if duplicate_pois:
            reasons.append(f"存在 {duplicate_pois} 个重复 POI，展示价值下降")

    if real_steps and rationale_count == len(real_steps):
        score += 3.0
        reasons.append("每个核心步骤都有可解释理由")
    elif real_steps:
        score += 1.0 * (rationale_count / len(real_steps))

    if events:
        score += min(4.0, 2.5 + len(events))
        reasons.append(f"包含 {len(events)} 次 reroute/风险处理，可展示 agent 调整能力")

    if diet_flags:
        score += 2.0
        reasons.append(f"覆盖偏好/禁忌：{', '.join(str(x) for x in diet_flags[:4])}")
    if any(word in user_input for word in ("乳糖", "寻麻疹", "荨麻疹", "忌口", "不能吃", "过敏")):
        score += 1.5
        reasons.append("用户输入含禁忌或健康约束，适合展示记忆/意图理解")

    if persona == "family" and (prefs.get("has_child") or "娃" in user_input or "孩子" in user_input):
        score += 2.0
        reasons.append("亲子画像明确，能体现儿童友好约束")
    elif persona == "with_parents" and any(word in user_input for word in ("爸", "妈", "父母", "老人")):
        score += 2.0
        reasons.append("陪父母画像明确，能体现慢节奏和舒适度")
    elif persona == "friends":
        score += 1.2
        reasons.append("朋友聚会画像明确，便于展示多人协同场景")
    elif persona == "solo":
        score += 1.0
        reasons.append("独自出行画像明确，便于展示轻量个人规划")

    if plan.get("summary"):
        score += 1.0
    if any((s.get("travel_time_min") or 0) > 0 for s in steps):
        score += 1.0
        reasons.append("步骤含真实路线耗时，适合配合地图展示")

    metrics = {
        "showcase_score": round(score, 2),
        "ok": True,
        "step_count": len(steps),
        "real_step_count": len(real_steps),
        "unique_poi_count": unique_pois,
        "duplicate_poi_count": duplicate_pois,
        "rationale_coverage": round(rationale_count / max(1, len(real_steps)), 3),
        "reroute_count": len(events),
        "has_diet_or_memory_signal": bool(diet_flags) or any(
            word in user_input for word in ("乳糖", "寻麻疹", "荨麻疹", "忌口", "不能吃", "过敏")
        ),
        "total_seconds": (result.get("timing") or {}).get("total_s"),
    }
    return score, reasons[:6], metrics


def select_showcases(
    scored: list[dict[str, Any]],
    *,
    selected_count: int,
) -> list[dict[str, Any]]:
    """Greedy selection: cover personas first, then maximize score/diversity."""
    eligible = [
        item for item in scored
        if item["score"] > 0 and item["result"].get("ok")
    ]
    ranked = sorted(eligible, key=lambda x: x["score"], reverse=True)
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    used_personas: set[str] = set()
    used_areas: set[str] = set()

    # First guarantee user-facing diversity: one best case per persona when possible.
    best_by_persona: dict[str, dict[str, Any]] = {}
    for item in ranked:
        persona = _input(item["result"]).get("persona") or ""
        if persona and persona not in best_by_persona:
            best_by_persona[persona] = item
    persona_representatives = sorted(
        best_by_persona.values(),
        key=lambda x: x["score"],
        reverse=True,
    )
    for item in persona_representatives[:selected_count]:
        selected.append(item)
        selected_ids.add(id(item))
        meta = _input(item["result"])
        used_personas.add(meta.get("persona") or "")
        used_areas.add(meta.get("area_anchor") or "")

    # Then prefer new areas among the remaining high-scoring cases.
    for item in ranked:
        if len(selected) >= selected_count:
            break
        if id(item) in selected_ids:
            continue
        meta = _input(item["result"])
        persona = meta.get("persona") or ""
        area = meta.get("area_anchor") or ""
        if area not in used_areas:
            selected.append(item)
            selected_ids.add(id(item))
            used_personas.add(persona)
            used_areas.add(area)

    for item in ranked:
        if len(selected) >= selected_count:
            break
        if id(item) not in selected_ids:
            selected.append(item)
            selected_ids.add(id(item))

    return selected[:selected_count]


def case_to_showcase(
    result: dict[str, Any],
    score: float,
    reasons: list[str],
    metrics: dict[str, Any],
    *,
    rank: int,
) -> dict[str, Any]:
    meta = _input(result)
    plan = _plan(result)
    steps = [
        {
            "order": s.get("step_index"),
            "time": s.get("start_time"),
            "kind": s.get("kind"),
            "poi_name": s.get("poi_name"),
            "duration_min": s.get("duration_min"),
            "mode_to_here": s.get("mode_to_here"),
            "travel_time_min": s.get("travel_time_min", 0),
            "rationale": s.get("rationale"),
            "is_rerouted": bool(s.get("is_rerouted")),
        }
        for s in (plan.get("steps") or [])
    ]
    events = [
        {
            "failed_poi_name": e.get("failed_poi_name"),
            "replacement_poi_name": e.get("replacement_poi_name"),
            "reason": e.get("reason"),
            "evidence": e.get("evidence") or [],
        }
        for e in (result.get("events") or [])
    ]
    return {
        "rank": rank,
        "scenario": result.get("scenario"),
        "title": result.get("title"),
        "persona": meta.get("persona"),
        "persona_zh": PERSONA_ZH.get(meta.get("persona"), meta.get("persona")),
        "area_anchor": meta.get("area_anchor"),
        "user_input": meta.get("user_input"),
        "prefs": meta.get("prefs") or {},
        "why_selected": reasons,
        "metrics": {**metrics, "showcase_score": round(score, 2)},
        "observed_result": {
            "summary": plan.get("summary"),
            "steps": steps,
            "reroute_events": events,
        },
        "timing": result.get("timing") or {},
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Showcase 测试案例",
        "",
        f"- 生成时间：{report.get('generated_at')}",
        f"- 后端：{report.get('backend')}",
        f"- 原始结果：{report.get('source_path')}",
        f"- 总候选数：{report.get('total_candidates_run')}",
        f"- 入选数：{report.get('selected_count')}",
    ]
    if report.get("excluded_scenarios"):
        lines.append(f"- 排除场景：{', '.join(report.get('excluded_scenarios') or [])}")
    lines.extend(["", "## 筛选标准", ""])
    for criterion in report.get("selection_criteria") or []:
        lines.append(f"- {criterion}")
    lines.append("")

    for case in report.get("selected_cases") or []:
        lines.extend([
            f"## {case['rank']}. {case['title']}（{case.get('persona_zh') or case.get('persona')}）",
            "",
            f"- 场景编号：{case.get('scenario')}",
            f"- 活动片区：{case.get('area_anchor')}",
            f"- 用户输入：{case.get('user_input')}",
            f"- Showcase 分：{case.get('metrics', {}).get('showcase_score')}",
            "",
            "### 为什么适合展示",
            "",
        ])
        for reason in case.get("why_selected") or []:
            lines.append(f"- {reason}")
        lines.extend(["", "### 规划结果", ""])
        summary = (case.get("observed_result") or {}).get("summary")
        if summary:
            lines.append(f"**总结**：{summary}")
            lines.append("")
        lines.append("| 顺序 | 时间 | 类型 | POI | 时长 | 理由 |")
        lines.append("|---|---|---|---|---:|---|")
        for step in (case.get("observed_result") or {}).get("steps") or []:
            rationale = _compact(str(step.get("rationale") or ""), 80)
            lines.append(
                f"| {step.get('order')} | {step.get('time')} | {step.get('kind')} | "
                f"{step.get('poi_name')} | {step.get('duration_min')} | {rationale} |"
            )
        events = (case.get("observed_result") or {}).get("reroute_events") or []
        if events:
            lines.extend(["", "### 换点/风险处理", ""])
            for event in events:
                lines.append(
                    f"- {event.get('failed_poi_name')} -> "
                    f"{event.get('replacement_poi_name') or '无替补'}"
                    f"（{event.get('reason')}）"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(report: dict[str, Any], json_out: Path, md_out: Path) -> None:
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_out.write_text(render_markdown(report), encoding="utf-8")


def _input(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("input") or {}


def _plan(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("v2") or result.get("v1") or {}


def _compact(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def _parse_ids(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="longcat", choices=["longcat", "mock", "dpsk", "deepseek", "anthropic"])
    parser.add_argument("--limit", type=int, default=40, help="跑前 N 个场景；0 表示全部")
    parser.add_argument("--skip", type=int, default=0, help="跳过前 N 个场景")
    parser.add_argument("--select", type=int, default=8, help="最终选择 4-10 条；脚本会裁剪到合法范围")
    parser.add_argument("--scenario-ids", default="", help="逗号分隔的场景 ID，例如 S01,S11,S31")
    parser.add_argument("--exclude-scenario-ids", default="", help="逗号分隔排除入选的场景 ID，例如 S35")
    parser.add_argument("--raw-out", default=str(DEFAULT_RAW_OUT))
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_OUT))
    parser.add_argument("--md-out", default=str(DEFAULT_MD_OUT))
    parser.add_argument("--reuse", default="", help="只从已有 raw JSON 选择，不重新调用 API")
    parser.add_argument("--no-resume", action="store_true", help="不复用 raw-out 里已有结果")
    args = parser.parse_args()

    selected_count = max(4, min(10, args.select))
    raw_out = Path(args.raw_out)
    json_out = Path(args.json_out)
    md_out = Path(args.md_out)

    if args.reuse:
        source = Path(args.reuse)
        results = json.loads(source.read_text(encoding="utf-8"))
        source_path = str(source)
    else:
        results = run_candidates(
            backend=args.backend,
            limit=args.limit,
            skip=args.skip,
            scenario_ids=_parse_ids(args.scenario_ids),
            raw_out=raw_out,
            resume=not args.no_resume,
        )
        source_path = str(raw_out)

    report = build_showcase_report(
        results,
        selected_count=selected_count,
        backend=args.backend,
        source_path=source_path,
        excluded_scenarios=_parse_ids(args.exclude_scenario_ids),
    )
    write_report(report, json_out, md_out)
    print(
        f"\nselected {report['selected_count']} / {report['total_candidates_run']} "
        f"-> {json_out} and {md_out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
