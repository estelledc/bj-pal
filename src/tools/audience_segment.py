"""POI 受众分层（[20] 改进点）。

从 UGC 文本启发式推断"该评价是 local 视角 vs tourist 视角"：
- local：含"本地""老北京""胡同里的""我家附近""周边居民""通勤"
- tourist：含"打卡""必去""网红""游客""排队""出片""第一次来""地标"
- expert：含"内行""深度""讲究""精选""考究"

按 POI 聚合 (local_count, tourist_count, expert_count) → audience_profile。

集成 rank_fuse：
- audience_preference="local"  → 偏向 local_count > tourist_count 的 POI（本地玩法）
- audience_preference="tourist" → 偏向 tourist_count 高的（必去地标）
- 缺省 None：不调整

应用：用户首次到北京 vs 本地老饕，推不同动线。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

AudienceLabel = Literal["local", "tourist", "expert", "mixed", "unknown"]
AudiencePreference = Literal["local", "tourist", "mixed"]


# ============================================================
# 关键词
# ============================================================

AUDIENCE_KEYWORDS: dict[str, list[str]] = {
    "local": [
        "本地", "老北京", "我家附近", "周边居民", "胡同里的",
        "本地人", "街坊", "常去", "常客", "通勤",
        "一个人去", "下班后", "本地食客", "本地居民",
    ],
    "tourist": [
        "打卡", "必去", "网红", "游客", "拍照", "出片",
        "外地", "第一次来", "排队", "旅游", "地标",
        "推荐景点", "外地游客", "必看", "标志性",
    ],
    "expert": [
        "内行", "懂行", "深度", "讲究", "细品",
        "进阶", "精选", "考究", "专业", "行家",
    ],
}


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AudienceProfile:
    poi_name: str
    local_count: int = 0
    tourist_count: int = 0
    expert_count: int = 0
    evidence: dict[str, list[str]] = field(default_factory=dict)

    def total(self) -> int:
        return self.local_count + self.tourist_count + self.expert_count

    def label(self, threshold: int = 2) -> AudienceLabel:
        """主导标签。差异 < threshold → mixed；都 0 → unknown。"""
        if self.total() == 0:
            return "unknown"
        scores = {
            "local": self.local_count,
            "tourist": self.tourist_count,
            "expert": self.expert_count,
        }
        top = max(scores, key=lambda k: scores[k])
        top_v = scores[top]
        # 检查领先优势
        others = [v for k, v in scores.items() if k != top]
        if others and (top_v - max(others)) < threshold:
            return "mixed"
        return top  # type: ignore

    def local_ratio(self) -> float:
        if self.total() == 0:
            return 0.0
        return self.local_count / self.total()

    def is_local_secret(self) -> bool:
        """本地秘籍：local_count ≥ 2 且 local_ratio > 0.6。"""
        return self.local_count >= 2 and self.local_ratio() > 0.6

    def is_tourist_must_go(self) -> bool:
        """游客必去：tourist_count ≥ 3 且 tourist > local。"""
        return self.tourist_count >= 3 and self.tourist_count > self.local_count


# ============================================================
# 索引
# ============================================================

_PROFILES: dict[str, AudienceProfile] = {}


def build_index(force_rebuild: bool = False) -> int:
    global _PROFILES
    if _PROFILES and not force_rebuild:
        return len(_PROFILES)

    from loader import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT poi_name, evidence_summary FROM ugc_aspects "
        "WHERE evidence_summary IS NOT NULL AND poi_name IS NOT NULL"
    ).fetchall()
    conn.close()

    profiles: dict[str, AudienceProfile] = {}
    for r in rows:
        poi = r["poi_name"]
        txt = r["evidence_summary"]
        prof = profiles.setdefault(poi, AudienceProfile(poi_name=poi))

        for label, kws in AUDIENCE_KEYWORDS.items():
            for kw in kws:
                if kw in txt:
                    setattr(prof, f"{label}_count", getattr(prof, f"{label}_count") + 1)
                    prof.evidence.setdefault(label, []).append(txt[:80])
                    break  # 同段同标签只算一次

    _PROFILES = profiles
    n_local = sum(1 for p in profiles.values() if p.is_local_secret())
    n_tourist = sum(1 for p in profiles.values() if p.is_tourist_must_go())
    logger.info(f"[audience] {len(profiles)} POI 索引，"
                f"local_secret={n_local}, tourist_must_go={n_tourist}")
    return len(profiles)


# ============================================================
# 查询
# ============================================================

def get_profile(poi_name: str) -> Optional[AudienceProfile]:
    if not _PROFILES:
        build_index()
    if not poi_name:
        return None
    if poi_name in _PROFILES:
        return _PROFILES[poi_name]
    # 前缀合并
    if len(poi_name) >= 3:
        prefix = poi_name[:3]
        merged = AudienceProfile(poi_name=poi_name)
        n = 0
        for name, prof in _PROFILES.items():
            if name.startswith(prefix) or poi_name in name or name in poi_name:
                merged.local_count += prof.local_count
                merged.tourist_count += prof.tourist_count
                merged.expert_count += prof.expert_count
                for label, evs in prof.evidence.items():
                    merged.evidence.setdefault(label, []).extend(evs)
                n += 1
        if n > 0:
            return merged
    return None


def get_audience_score_adjust(
    poi_name: str,
    preference: Optional[AudiencePreference] = None,
) -> tuple[float, str]:
    """根据用户视角偏好给出 score 调整。

    - preference="local" 时：local_secret POI +0.10，tourist_must_go -0.10
    - preference="tourist"：反过来
    - preference="mixed" / None：不调整
    """
    if preference is None or preference == "mixed":
        return 0.0, ""
    prof = get_profile(poi_name)
    if prof is None:
        return 0.0, ""

    if preference == "local":
        if prof.is_local_secret():
            return 0.10, f"🏠 {poi_name} 是本地玩法（{prof.local_count} 条 UGC 提"\
                          f"老北京/胡同/街坊视角）"
        if prof.is_tourist_must_go():
            return -0.08, f"⚠️ {poi_name} 偏游客地标（{prof.tourist_count} 条 UGC 提"\
                           f"打卡/网红/外地游客）— 想要本地玩法可换备选"
    elif preference == "tourist":
        if prof.is_tourist_must_go():
            return 0.10, f"📸 {poi_name} 是游客必去地标（{prof.tourist_count} 条 UGC 提"\
                          f"打卡/必去/网红视角）"
        if prof.is_local_secret():
            return -0.05, f"⚠️ {poi_name} 偏本地玩法（{prof.local_count} 条 UGC 提"\
                          f"本地视角）— 第一次来北京可能不容易体验到"
    return 0.0, ""


# ============================================================
# 全局检索
# ============================================================

def get_top_local_secrets(top_k: int = 10) -> list[AudienceProfile]:
    if not _PROFILES:
        build_index()
    pool = [p for p in _PROFILES.values() if p.is_local_secret()]
    pool.sort(key=lambda p: p.local_count, reverse=True)
    return pool[:top_k]


def get_top_tourist_landmarks(top_k: int = 10) -> list[AudienceProfile]:
    if not _PROFILES:
        build_index()
    pool = [p for p in _PROFILES.values() if p.is_tourist_must_go()]
    pool.sort(key=lambda p: p.tourist_count, reverse=True)
    return pool[:top_k]


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import time
    t0 = time.time()
    n = build_index()
    print(f"index built: {n} POI in {time.time()-t0:.2f}s")

    print("\n=== 本地秘籍 Top 8（local_secret = local≥2 且 local_ratio>0.6）===")
    for p in get_top_local_secrets(top_k=8):
        ev = p.evidence.get("local", [""])[0][:70]
        print(f"  · {p.poi_name:25s} local={p.local_count} tourist={p.tourist_count}")
        print(f"      {ev}")

    print("\n=== 游客必去 Top 8（tourist_must_go = tourist≥3 且 tourist>local）===")
    for p in get_top_tourist_landmarks(top_k=8):
        ev = p.evidence.get("tourist", [""])[0][:70]
        print(f"  · {p.poi_name:25s} tourist={p.tourist_count} local={p.local_count}")
        print(f"      {ev}")

    print("\n=== preference 触发 ===")
    samples = ["故宫博物院", "南锣鼓巷", "护国寺小吃", "雍和宫", "西海西沿"]
    for name in samples:
        prof = get_profile(name)
        if not prof:
            continue
        print(f"\n  {name}: local={prof.local_count} tourist={prof.tourist_count} "
              f"label={prof.label()}")
        for pref in ["local", "tourist"]:
            d, why = get_audience_score_adjust(name, pref)  # type: ignore
            if d != 0:
                print(f"    pref={pref}: adjust={d:+.2f}")
