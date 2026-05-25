"""古建预约规则查询（[10] 改进点）。

加载 data/heritage_reservations.json，提供：
- get_rule(poi_name) → ReservationRule | None
- requires_reservation(poi_name, dt) → bool
- get_release_window(poi_name, target_date) → 当前是否在释票窗内？
- check_feasibility(poi_name, target_dt, party_size) → ReservationCheck

集成进 availability_probe 后：planner 推一个未预约的限流景点 → probe 触发 reroute。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "heritage_reservations.json"

_RULES_CACHE: Optional[dict] = None
_ALIAS_INDEX: dict[str, str] = {}


def _load() -> dict:
    """懒加载 + 别名索引。"""
    global _RULES_CACHE, _ALIAS_INDEX
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    with DATA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    _RULES_CACHE = data["rules"]
    _ALIAS_INDEX = {}
    for canonical, rule in _RULES_CACHE.items():
        _ALIAS_INDEX[canonical] = canonical
        for alias in rule.get("name_aliases", []):
            _ALIAS_INDEX[alias] = canonical
    logger.info(f"[reservation] loaded {len(_RULES_CACHE)} rules, "
                f"{len(_ALIAS_INDEX) - len(_RULES_CACHE)} aliases")
    return _RULES_CACHE


@dataclass
class ReservationRule:
    canonical_name: str
    requires_reservation: bool
    release_lead_days: int = 0
    release_time: str = "00:00"          # "HH:MM"
    sessions: list[str] = field(default_factory=lambda: ["full"])
    session_split: Optional[str] = None  # "12:00" 上下午分场
    session_transferable: bool = True
    max_per_id_per_day: int = 1
    weekly_close_day: Optional[str] = None  # "monday" / null
    winter_close_pm: bool = False
    release_url: str = ""
    notes: str = ""


def _build_rule(canonical: str, raw: dict) -> ReservationRule:
    return ReservationRule(
        canonical_name=canonical,
        requires_reservation=raw.get("requires_reservation", False),
        release_lead_days=raw.get("release_lead_days", 0),
        release_time=raw.get("release_time", "00:00"),
        sessions=raw.get("sessions", ["full"]),
        session_split=raw.get("session_split"),
        session_transferable=raw.get("session_transferable", True),
        max_per_id_per_day=raw.get("max_per_id_per_day", 1),
        weekly_close_day=raw.get("weekly_close_day"),
        winter_close_pm=raw.get("winter_close_pm", False),
        release_url=raw.get("release_url", ""),
        notes=raw.get("notes", ""),
    )


def get_rule(poi_name: str) -> Optional[ReservationRule]:
    """模糊匹配 POI 名字到规则；找不到返 None。"""
    rules = _load()
    if not poi_name:
        return None
    # 精确 / 别名
    if poi_name in _ALIAS_INDEX:
        canonical = _ALIAS_INDEX[poi_name]
        return _build_rule(canonical, rules[canonical])
    # 子串模糊（POI 名是 "故宫博物院（午门）" 这类）
    for alias, canonical in _ALIAS_INDEX.items():
        if alias in poi_name or poi_name in alias:
            return _build_rule(canonical, rules[canonical])
    return None


# ============================================================
# 可行性检查
# ============================================================

@dataclass
class ReservationCheck:
    poi_name: str
    requires_reservation: bool
    feasible: bool                 # 这次出行是否可行
    reason: str                    # 不可行的原因（一句话）
    rule: Optional[ReservationRule] = None
    days_until_target: int = 0
    days_too_late: int = 0          # 提前多少天才能约（如果不行）
    closes_today: bool = False      # 周一闭馆
    fallback_action: str = ""       # "reroute" / "warn" / "ok"


def check_feasibility(
    poi_name: str,
    target_dt: datetime,
    now: Optional[datetime] = None,
) -> ReservationCheck:
    """检查 target_dt 是否能成功预约该 POI。

    规则：
    - 如果不需预约 → feasible=True
    - 如果 target 在 release_lead_days 内 → feasible=False（来不及约）
    - 如果 target 是 weekly_close_day → feasible=False
    """
    rule = get_rule(poi_name)
    if rule is None:
        return ReservationCheck(
            poi_name=poi_name,
            requires_reservation=False,
            feasible=True,
            reason="无预约规则记录（默认放行）",
            fallback_action="ok",
        )
    if not rule.requires_reservation:
        return ReservationCheck(
            poi_name=poi_name,
            requires_reservation=False,
            feasible=True,
            reason="该 POI 不需预约",
            rule=rule,
            fallback_action="ok",
        )

    now = now or datetime.now()
    days = (target_dt.date() - now.date()).days

    # 周一闭馆
    weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                   "friday": 4, "saturday": 5, "sunday": 6}
    if rule.weekly_close_day and target_dt.weekday() == weekday_map.get(rule.weekly_close_day):
        return ReservationCheck(
            poi_name=poi_name,
            requires_reservation=True,
            feasible=False,
            reason=f"{rule.canonical_name} 每周{rule.weekly_close_day}闭馆",
            rule=rule,
            days_until_target=days,
            closes_today=True,
            fallback_action="reroute",
        )

    # 释票窗：当前已超出最早释票时间但还在 lead_days 之内？
    # 简化判定：只看 lead_days
    # 如果 target 在 lead_days 之内（含同一天），且当前未到释票时间 → 来不及
    if days < 0:
        return ReservationCheck(
            poi_name=poi_name,
            requires_reservation=True,
            feasible=False,
            reason="目标日期已过",
            rule=rule, days_until_target=days,
            fallback_action="reroute",
        )
    if days >= rule.release_lead_days:
        # 票早就放出来了
        return ReservationCheck(
            poi_name=poi_name,
            requires_reservation=True,
            feasible=True,  # 但要注意是否抢得到——不在算法范围内
            reason=f"释票已开放（提前 {rule.release_lead_days} 天）；建议立即去 {rule.release_url}",
            rule=rule, days_until_target=days,
            fallback_action="warn",
        )

    # 还在 lead_days 内：可能还能约
    days_too_late = rule.release_lead_days - days
    return ReservationCheck(
        poi_name=poi_name,
        requires_reservation=True,
        feasible=False,
        reason=(
            f"{rule.canonical_name} 需提前 {rule.release_lead_days} 天 {rule.release_time} 释票，"
            f"距出行仅 {days} 天，预约窗口已关。建议改期或换备选。"
        ),
        rule=rule,
        days_until_target=days,
        days_too_late=days_too_late,
        fallback_action="reroute",
    )


# ============================================================
# 列表
# ============================================================

def list_reservation_required_pois() -> list[str]:
    """所有需预约的 POI canonical name。"""
    rules = _load()
    return [name for name, r in rules.items() if r.get("requires_reservation")]


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    # 加载
    rules = _load()
    print(f"=== 加载 {len(rules)} 条规则 ===")

    # 别名匹配
    cases = ["故宫", "国博", "雍和宫", "奥森", "不存在景点"]
    print("\n=== 别名匹配 ===")
    for name in cases:
        r = get_rule(name)
        if r:
            print(f"  ✓ {name:10s} → {r.canonical_name}（需预约={r.requires_reservation}）")
        else:
            print(f"  · {name:10s} → 无规则")

    # 可行性 — 5/21 想去故宫，5/23 出行（仅提前 2 天，故宫要 7 天）
    print("\n=== 可行性 ===")
    now = datetime(2026, 5, 21, 14, 0)  # 现在 5/21 下午
    weekend = datetime(2026, 5, 23, 14, 0)  # 周六
    next_week = datetime(2026, 5, 30, 14, 0)  # 9 天后
    monday = datetime(2026, 6, 1, 14, 0)  # 周一（国博闭馆）

    for poi, target in [
        ("故宫", weekend),
        ("故宫", next_week),
        ("国家博物馆", weekend),
        ("国家博物馆", monday),
        ("奥林匹克森林公园", weekend),
    ]:
        chk = check_feasibility(poi, target, now=now)
        flag = "✓" if chk.feasible else "✗"
        print(f"  {flag} {poi:15s} {target:%Y-%m-%d %a}: {chk.reason}")

    # 总数
    pois = list_reservation_required_pois()
    print(f"\n=== 共 {len(pois)} 个需预约景点 ===")
    for p in pois[:5]:
        print(f"  · {p}")
    print(f"  ... +{len(pois)-5} more")
