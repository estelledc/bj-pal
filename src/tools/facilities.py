"""POI facility 字段抽取（[01] 改进点）。

从 6300 UGC 抽 5 类 facility 信号：
- toilet（厕所/卫生间）
- baby（母婴室/哺乳室/儿童设施）
- wheelchair（无障碍/轮椅/电梯/斜坡）
- charging（充电桩/插座）
- parking（停车场/车位）

每类信号取值 ∈ {+1, 0, -1}：
- +1：UGC 提及该 facility 且 sentiment=positive（"齐全"/"友好"）
- -1：UGC 提及但 sentiment=negative（"难找"/"脏"）+ 关键短语（"缺乏"/"不便"）
- 0：未提及（不能因为没数据而降权）

集成进 rank_fuse：
- prefs.has_child + 5 岁以下娃 → 优先 baby+1 的 POI；baby=-1 排除
- prefs.wheelchair → 必须 wheelchair ≥ 0
- prefs.driving → 优先 parking+1
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


# ============================================================
# 关键词库
# ============================================================

FACILITY_KEYWORDS = {
    "toilet":     ["厕所", "卫生间", "洗手间", "公厕", "第三卫生间"],
    "baby":       ["母婴室", "哺乳", "婴儿", "推车", "童车", "母婴", "亲子设施"],
    "wheelchair": ["无障碍", "轮椅", "电梯", "斜坡", "残障", "扶手"],
    "charging":   ["充电", "插座", "快充", "充电桩"],
    "parking":    ["停车", "车位", "地下停车", "停车场"],
}

# 负向短语（即便 sentiment 是 mixed/positive 也视作"该 facility 缺位"）
NEGATIVE_PHRASES = {
    "toilet":     ["卫生间脏", "厕所难找", "无卫生间", "卫生间偏远"],
    "baby":       ["缺乏母婴", "无母婴室", "婴儿不便", "推车难推", "不适合带娃", "不友好婴儿"],
    "wheelchair": ["无电梯", "台阶多", "轮椅难", "无障碍欠缺"],
    "parking":    ["停车困难", "停车不便", "停车位少", "无停车场", "停车贵"],
    "charging":   ["无充电", "无插座", "充电难"],
}


# ============================================================
# 数据类型
# ============================================================

@dataclass
class FacilityProfile:
    poi_name: str
    toilet: int = 0
    baby: int = 0
    wheelchair: int = 0
    charging: int = 0
    parking: int = 0
    evidence: dict[str, list[str]] = field(default_factory=dict)

    def is_kid_friendly(self) -> bool:
        return self.baby >= 1 and self.toilet >= 0

    def is_wheelchair_friendly(self) -> bool:
        return self.wheelchair >= 1

    def is_driver_friendly(self) -> bool:
        return self.parking >= 1

    def has_blocker_for(self, kind: str) -> bool:
        return getattr(self, kind, 0) <= -1


# ============================================================
# 索引构建
# ============================================================

_PROFILES: dict[str, FacilityProfile] = {}


def build_index(force_rebuild: bool = False) -> int:
    """扫 UGC 建 POI → FacilityProfile 字典。"""
    global _PROFILES
    if _PROFILES and not force_rebuild:
        return len(_PROFILES)

    from loader import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT poi_name, sentiment, evidence_summary "
        "FROM ugc_aspects WHERE evidence_summary IS NOT NULL"
    ).fetchall()
    conn.close()

    profiles: dict[str, FacilityProfile] = {}
    for r in rows:
        poi = r["poi_name"]
        if not poi:
            continue
        sent = r["sentiment"] or "neutral"
        txt = r["evidence_summary"]
        prof = profiles.setdefault(poi, FacilityProfile(poi_name=poi))

        for facet, kws in FACILITY_KEYWORDS.items():
            kw_match = any(kw in txt for kw in kws)
            neg_match = any(p in txt for p in NEGATIVE_PHRASES.get(facet, []))

            if neg_match:
                # 负向短语优先，强制 -1
                cur = getattr(prof, facet)
                setattr(prof, facet, min(cur, -1))
                prof.evidence.setdefault(facet, []).append(f"⚠ {txt[:80]}")
            elif kw_match:
                if sent == "positive":
                    cur = getattr(prof, facet)
                    setattr(prof, facet, max(cur, 1))
                    prof.evidence.setdefault(facet, []).append(txt[:80])
                elif sent == "negative":
                    cur = getattr(prof, facet)
                    setattr(prof, facet, min(cur, -1))
                    prof.evidence.setdefault(facet, []).append(f"⚠ {txt[:80]}")
                # mixed/neutral 不动

    _PROFILES = profiles
    n_kid = sum(1 for p in profiles.values() if p.is_kid_friendly())
    n_wc = sum(1 for p in profiles.values() if p.is_wheelchair_friendly())
    n_drive = sum(1 for p in profiles.values() if p.is_driver_friendly())
    logger.info(f"[facilities] {len(profiles)} POI, kid_friendly={n_kid}, "
                f"wheelchair={n_wc}, driver={n_drive}")
    return len(profiles)


# ============================================================
# 查询接口
# ============================================================

def get_profile(poi_name: str) -> Optional[FacilityProfile]:
    if not _PROFILES:
        build_index()
    if not poi_name:
        return None
    # 始终前缀合并：UGC 经常用 "X设施" / "X餐饮" / "X(子POI)" 拆分同一 POI
    if len(poi_name) >= 3:
        prefix = poi_name[:3]
        merged = FacilityProfile(poi_name=poi_name)
        n = 0
        for name, prof in _PROFILES.items():
            if name.startswith(prefix) or poi_name in name or name in poi_name:
                for f in ("toilet", "baby", "wheelchair", "charging", "parking"):
                    cur = getattr(merged, f)
                    other = getattr(prof, f)
                    # 合并：负向优先（min），否则 max
                    if other < 0 or cur < 0:
                        setattr(merged, f, min(cur, other))
                    else:
                        setattr(merged, f, max(cur, other))
                for facet, evs in prof.evidence.items():
                    merged.evidence.setdefault(facet, []).extend(evs)
                n += 1
        if n > 0:
            return merged
    return _PROFILES.get(poi_name)


def filter_by_constraints(
    pois: list,
    has_child: bool = False,
    child_age: Optional[int] = None,
    wheelchair: bool = False,
    driving: bool = False,
) -> list:
    """根据用户约束硬过滤 facility blocker 的 POI。

    规则：
    - has_child + child_age ≤ 5：移除 baby ≤ -1（明确不友好）的 POI
    - wheelchair：移除 wheelchair ≤ -1
    - driving：parking ≤ -1 减分但不剔除（怕过度严格）
    """
    out = []
    for p in pois:
        name = getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None)
        if not name:
            out.append(p)
            continue
        prof = get_profile(name)
        if prof is None:
            out.append(p)  # 缺数据放行
            continue
        if has_child and child_age is not None and child_age <= 5:
            if prof.baby <= -1:
                continue
        if wheelchair and prof.wheelchair <= -1:
            continue
        out.append(p)
    return out


def get_facility_score_adjust(
    poi_name: str,
    has_child: bool = False,
    wheelchair: bool = False,
    driving: bool = False,
) -> tuple[float, list[str]]:
    """根据用户需求给 POI 算 facility 加分 / 减分（用于 rank_fuse 集成）。

    Returns: (score_delta, reasons)
    """
    prof = get_profile(poi_name)
    if prof is None:
        return 0.0, []
    delta = 0.0
    reasons: list[str] = []

    if has_child:
        if prof.baby >= 1:
            delta += 0.05
            reasons.append(f"👶 母婴友好（{prof.evidence.get('baby', [''])[0][:50]}）")
        elif prof.baby <= -1:
            delta -= 0.10
            reasons.append("⚠️ UGC 提及对娃不友好（推车难推 / 缺母婴室）")
    if wheelchair:
        if prof.wheelchair >= 1:
            delta += 0.10
            reasons.append("♿ 无障碍设施齐全")
        elif prof.wheelchair <= -1:
            delta -= 0.20
            reasons.append("⚠️ 无电梯 / 台阶多 / 轮椅难")
    if driving:
        if prof.parking >= 1:
            delta += 0.03
        elif prof.parking <= -1:
            delta -= 0.08
            reasons.append("⚠️ 停车困难 / 停车位少")

    return round(delta, 4), reasons


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import time
    t0 = time.time()
    n = build_index()
    print(f"index built: {n} POI in {time.time()-t0:.2f}s")

    # 头部 baby_friendly POI
    print("\n=== 母婴友好 top 8 ===")
    kid_pois = [p for p in _PROFILES.values() if p.is_kid_friendly()]
    for p in sorted(kid_pois, key=lambda x: -x.baby)[:8]:
        print(f"  · {p.poi_name}  toilet={p.toilet} baby={p.baby} parking={p.parking}")

    print("\n=== 单查 ===")
    for name in ["朝阳大悦城", "故宫博物院", "蓝色港湾", "三里屯太古里"]:
        prof = get_profile(name)
        if prof:
            print(f"  {name:25s} toilet={prof.toilet:+d} baby={prof.baby:+d} "
                  f"wheelchair={prof.wheelchair:+d} parking={prof.parking:+d}")
        else:
            print(f"  {name:25s} 无数据")

    print("\n=== 用户约束打分 ===")
    for name in ["朝阳大悦城", "故宫博物院"]:
        delta, reasons = get_facility_score_adjust(
            name, has_child=True, wheelchair=False, driving=True)
        print(f"  {name}: 带娃+开车 → adjust={delta:+.3f}")
        for r in reasons:
            print(f"      {r}")
