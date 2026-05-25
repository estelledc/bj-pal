"""TravelPlanner 风格 evaluator（[83] 改进点）。

参考 NeurIPS 2024 TravelPlanner (arxiv:2402.01622) 的 4 指标范式：

| 指标 | 含义 | 判定方式 |
|---|---|---|
| delivery_rate | 端到端完成率 | run_one 没抛异常 |
| commonsense_pass | plan 通过常识检查 | 6 个子项（步数 / POI 白名单 / 时间递增 / mode 合法 / 时间不超 / 不重复） |
| hard_constraint_pass | 满足用户硬约束 | 4 个子项（预算 / 步行半径 / 总时长 / persona 适配） |
| final_pass | 三项全过 | AND |

每项硬指标可单独评分（0-1），final_pass 是布尔 AND。

用法：
    python -m evals.eval_plans --input data/longcat_demo_results.json
    python -m evals.eval_plans --compare data/longcat_demo_results.json data/longcat_demo_v2.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# 复用项目里的 POI 白名单（loader 建好的 SQLite）
from loader import get_conn  # noqa: E402


# ============================================================
# Commonsense checks（共 6 项）
# ============================================================

def check_step_count(steps: list[dict]) -> tuple[bool, str]:
    """5-7 步是命题硬性要求；含 depart 收尾步可放宽到 4-8。"""
    n = len(steps)
    if 4 <= n <= 8:
        return True, ""
    return False, f"步数={n} 不在 [4,8]"


_POI_WHITELIST: set[str] | None = None


def _poi_whitelist() -> set[str]:
    global _POI_WHITELIST
    if _POI_WHITELIST is None:
        conn = get_conn()
        rows = conn.execute("SELECT id FROM pois").fetchall()
        conn.close()
        _POI_WHITELIST = {r["id"] for r in rows}
    return _POI_WHITELIST


def check_poi_whitelist(steps: list[dict]) -> tuple[bool, str]:
    """每步 poi_id 必须在 amap 白名单（除 depart 收尾步可空）。"""
    bad = []
    wl = _poi_whitelist()
    for s in steps:
        pid = s.get("poi_id")
        if pid is None:
            if s.get("kind") == "depart":
                continue
            bad.append(f"#{s.get('step_index')} 无 poi_id")
            continue
        if pid not in wl:
            bad.append(f"#{s.get('step_index')} {pid}")
    if bad:
        return False, f"非法 POI: {','.join(bad[:3])}"
    return True, ""


def check_time_monotonic(steps: list[dict]) -> tuple[bool, str]:
    """start_time 递增，且加 duration 不超过下一步 start_time。"""
    last_end = None
    for s in steps:
        t = _hh(s.get("start_time"))
        d = s.get("duration_min") or 0
        if t is None:
            return False, f"#{s.get('step_index')} 无 start_time"
        if last_end is not None and t < last_end:
            return False, f"#{s.get('step_index')} 时间倒流 {s.get('start_time')} < end={_fmt(last_end)}"
        last_end = t + d
    return True, ""


_VALID_MODES = {"walking", "bicycling", "driving", "transit"}


def check_mode_valid(steps: list[dict]) -> tuple[bool, str]:
    bad = [s.get("step_index") for s in steps
           if s.get("mode_to_here") and s.get("mode_to_here") not in _VALID_MODES]
    if bad:
        return False, f"非法 mode 步骤: {bad}"
    return True, ""


def check_within_duration(steps: list[dict], duration_hours: float) -> tuple[bool, str]:
    """总时长（最后步 start - 第一步 start + duration）≤ user spec."""
    if not steps:
        return False, "空 plan"
    first = _hh(steps[0].get("start_time"))
    last = _hh(steps[-1].get("start_time"))
    if first is None or last is None:
        return True, ""  # 时间字段问题已被 check_time_monotonic 抓
    last_dur = steps[-1].get("duration_min") or 0
    total_min = (last + last_dur) - first
    cap_min = int(duration_hours * 60) + 30  # 留 30 分钟缓冲
    if total_min > cap_min:
        return False, f"总时长 {total_min}min > {cap_min}min（spec={duration_hours}h+30）"
    return True, ""


def check_no_duplicate_pois(steps: list[dict]) -> tuple[bool, str]:
    """同一个 POI 在 plan 里重复出现（多于 2 次）视为编排失败。"""
    seen: dict[str, int] = {}
    for s in steps:
        pid = s.get("poi_id")
        if pid:
            seen[pid] = seen.get(pid, 0) + 1
    dups = [pid for pid, n in seen.items() if n > 1]
    if dups:
        return False, f"POI 重复: {dups}"
    return True, ""


COMMONSENSE_CHECKS = [
    ("step_count", check_step_count),
    ("poi_whitelist", check_poi_whitelist),
    ("time_monotonic", check_time_monotonic),
    ("mode_valid", check_mode_valid),
    ("no_duplicate_pois", check_no_duplicate_pois),
]


# ============================================================
# Hard constraint checks（共 4 项）
# ============================================================

def check_budget(steps: list[dict], prefs: dict, pois_lookup: dict) -> tuple[bool, str]:
    """meal/snack/rest 类步骤的人均价格之和 ≤ budget × 步数。

    宽松判定：每个 meal step 单独 ≤ budget × 1.2 即可（一顿饭超 1.2 倍认为越线）。
    """
    cap = prefs.get("budget_per_person")
    if not cap:
        return True, ""
    bad = []
    for s in steps:
        if s.get("kind") not in ("meal", "snack", "rest"):
            continue
        pid = s.get("poi_id")
        poi = pois_lookup.get(pid)
        if not poi:
            continue
        price = poi.get("avg_price")
        if price and price > cap * 1.2:
            bad.append(f"#{s.get('step_index')} {s.get('poi_name')}={price}>{cap}×1.2")
    if bad:
        return False, "; ".join(bad[:2])
    return True, ""


def check_within_duration_hard(steps: list[dict], prefs: dict) -> tuple[bool, str]:
    return check_within_duration(steps, prefs.get("duration_hours", 4.0))


def check_walk_radius(steps: list[dict], prefs: dict, pois_lookup: dict) -> tuple[bool, str]:
    """相邻步骤为 walking 时，距离 ≤ walk_radius_km。

    走 driving / transit / bicycling 不限。
    """
    cap_km = prefs.get("walk_radius_km")
    if not cap_km:
        return True, ""
    prev_coord = None
    bad = []
    for s in steps:
        pid = s.get("poi_id")
        poi = pois_lookup.get(pid)
        cur = (poi.get("longitude"), poi.get("latitude")) if poi else None
        if prev_coord and cur and s.get("mode_to_here") == "walking":
            d = _haversine_km(prev_coord[0], prev_coord[1], cur[0], cur[1])
            if d > cap_km * 1.5:  # 1.5× 缓冲
                bad.append(f"#{s.get('step_index')} {d:.2f}km>{cap_km}×1.5")
        prev_coord = cur if cur and cur[0] else prev_coord
    if bad:
        return False, "; ".join(bad[:2])
    return True, ""


def check_persona_dietary(steps: list[dict], prefs: dict, pois_lookup: dict) -> tuple[bool, str]:
    """no_spicy / light_diet 等 diet_flags 应在 meal step 体现。

    宽松判定：no_spicy 时 meal 步 POI 名不应含 '辣 / 麻辣 / 川 / 火锅'；
    light_diet 时不应是高油大餐（炸 / 烤）。
    """
    flags = prefs.get("diet_flags") or []
    if not flags:
        return True, ""
    bad = []
    blacklist_no_spicy = ("辣", "麻辣", "川", "火锅", "毛血旺", "水煮鱼")
    blacklist_light = ("烤鸭", "炸鸡", "烤肉", "毛血旺", "麻辣")
    for s in steps:
        if s.get("kind") not in ("meal", "snack"):
            continue
        name = s.get("poi_name") or ""
        if "no_spicy" in flags and any(kw in name for kw in blacklist_no_spicy):
            bad.append(f"no_spicy 但 #{s.get('step_index')} {name}")
        if "light_diet" in flags and any(kw in name for kw in blacklist_light):
            bad.append(f"light_diet 但 #{s.get('step_index')} {name}")
    if bad:
        return False, "; ".join(bad[:2])
    return True, ""


HARD_CHECKS = [
    ("budget", check_budget),
    ("walk_radius", check_walk_radius),
    ("within_duration", lambda steps, prefs, _l: check_within_duration_hard(steps, prefs)),
    ("persona_dietary", check_persona_dietary),
]


# ============================================================
# Helpers
# ============================================================

def _hh(time_str: str | None) -> int | None:
    if not time_str:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})", time_str)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _fmt(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _haversine_km(lng1, lat1, lng2, lat2):
    from math import radians, sin, cos, asin, sqrt
    if any(v is None for v in (lng1, lat1, lng2, lat2)):
        return 0.0
    lng1, lat1, lng2, lat2 = map(radians, [lng1, lat1, lng2, lat2])
    dlng = lng2 - lng1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


_POIS_LOOKUP: dict[str, dict] | None = None


def _pois_lookup() -> dict[str, dict]:
    global _POIS_LOOKUP
    if _POIS_LOOKUP is None:
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, name, avg_price, longitude, latitude FROM pois"
        ).fetchall()
        conn.close()
        _POIS_LOOKUP = {r["id"]: dict(r) for r in rows}
    return _POIS_LOOKUP


# ============================================================
# 主评估
# ============================================================

def evaluate_one(scenario_result: dict, plan_key: str = "v2") -> dict:
    """对一条场景（v1 或 v2 plan）算 4 个指标。"""
    if not scenario_result.get("ok"):
        return {
            "scenario": scenario_result.get("scenario"),
            "delivery": False,
            "commonsense": {"_overall": False, "reason": "未交付"},
            "hard": {"_overall": False, "reason": "未交付"},
            "final": False,
        }

    plan = scenario_result.get(plan_key) or {}
    steps = plan.get("steps") or []
    prefs = scenario_result.get("input", {}).get("prefs") or {}
    pois_lookup = _pois_lookup()

    # commonsense
    cs_results = {}
    cs_pass = True
    for name, fn in COMMONSENSE_CHECKS:
        ok, reason = fn(steps)
        cs_results[name] = {"ok": ok, "reason": reason} if not ok else {"ok": True}
        cs_pass = cs_pass and ok

    # hard constraint
    hd_results = {}
    hd_pass = True
    for name, fn in HARD_CHECKS:
        ok, reason = fn(steps, prefs, pois_lookup)
        hd_results[name] = {"ok": ok, "reason": reason} if not ok else {"ok": True}
        hd_pass = hd_pass and ok

    return {
        "scenario": scenario_result.get("scenario"),
        "title": scenario_result.get("title"),
        "delivery": True,
        "commonsense": {"_overall": cs_pass, **cs_results},
        "hard": {"_overall": hd_pass, **hd_results},
        "final": cs_pass and hd_pass,
    }


def evaluate_run(results: list[dict], plan_key: str = "v2") -> dict:
    per = [evaluate_one(r, plan_key) for r in results]
    n = len(per)
    n_delivery = sum(1 for x in per if x["delivery"])
    n_cs = sum(1 for x in per if x["commonsense"]["_overall"])
    n_hd = sum(1 for x in per if x["hard"]["_overall"])
    n_final = sum(1 for x in per if x["final"])
    return {
        "plan_key": plan_key,
        "total": n,
        "metrics": {
            "delivery_rate": round(n_delivery / n, 3) if n else 0,
            "commonsense_pass": round(n_cs / n, 3) if n else 0,
            "hard_constraint_pass": round(n_hd / n, 3) if n else 0,
            "final_pass": round(n_final / n, 3) if n else 0,
        },
        "counts": {
            "delivery": f"{n_delivery}/{n}",
            "commonsense": f"{n_cs}/{n}",
            "hard": f"{n_hd}/{n}",
            "final": f"{n_final}/{n}",
        },
        "per_scenario": per,
    }


def print_summary(report: dict, label: str = ""):
    m = report["metrics"]
    c = report["counts"]
    print(f"\n=== {label or report['plan_key']} ({report['total']} 场景) ===")
    print(f"  delivery_rate         {m['delivery_rate']:.3f}  ({c['delivery']})")
    print(f"  commonsense_pass      {m['commonsense_pass']:.3f}  ({c['commonsense']})")
    print(f"  hard_constraint_pass  {m['hard_constraint_pass']:.3f}  ({c['hard']})")
    print(f"  final_pass            {m['final_pass']:.3f}  ({c['final']})")


def print_failure_details(report: dict, max_show: int = 6):
    print("\n失败明细（前 {} 个）：".format(max_show))
    shown = 0
    for s in report["per_scenario"]:
        if s["final"]:
            continue
        reasons = []
        if not s["delivery"]:
            reasons.append("未交付")
        for k, v in s["commonsense"].items():
            if k == "_overall" or not isinstance(v, dict):
                continue
            if not v.get("ok"):
                reasons.append(f"{k}: {v.get('reason')}")
        for k, v in s["hard"].items():
            if k == "_overall" or not isinstance(v, dict):
                continue
            if not v.get("ok"):
                reasons.append(f"{k}: {v.get('reason')}")
        print(f"  {s['scenario']} {s.get('title','')}: {' | '.join(reasons[:3])}")
        shown += 1
        if shown >= max_show:
            break


def compare(reports: dict[str, dict]):
    print("\n=== 对比 ===")
    print(f"{'指标':<22}", *(f"{label:>14}" for label in reports))
    for k in ("delivery_rate", "commonsense_pass", "hard_constraint_pass", "final_pass"):
        row = [f"{reports[label]['metrics'][k]:.3f}" for label in reports]
        print(f"{k:<22}", *(f"{v:>14}" for v in row))


# ============================================================
# CLI
# ============================================================

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", help="单个 results.json 路径")
    p.add_argument("--compare", nargs="+", metavar="LABEL=PATH",
                   help="多份 results.json 对比，格式 v1=path/to/v1.json v2=path/to/v2.json")
    p.add_argument("--plan-key", default="v2", choices=["v1", "v2"],
                   help="对哪一版打分（默认 v2，含 reroute 后）")
    p.add_argument("--out", help="把详细 per-scenario 评估结果写到这里")
    p.add_argument("--no-details", action="store_true", help="不打印失败明细")
    args = p.parse_args()

    if args.input and args.compare:
        p.error("--input 和 --compare 二选一")

    if args.input:
        results = json.loads(Path(args.input).read_text())
        report = evaluate_run(results, plan_key=args.plan_key)
        print_summary(report, label=Path(args.input).stem)
        if not args.no_details:
            print_failure_details(report)
        if args.out:
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"\n已写入 {args.out}")
        return

    if args.compare:
        reports: dict[str, dict] = {}
        for spec in args.compare:
            label, _, path = spec.partition("=")
            if not path:
                p.error(f"--compare 项格式错误：{spec}（期望 LABEL=PATH）")
            results = json.loads(Path(path).read_text())
            reports[label] = evaluate_run(results, plan_key=args.plan_key)
            print_summary(reports[label], label=label)
        compare(reports)
        return

    p.print_help()


if __name__ == "__main__":
    main()
