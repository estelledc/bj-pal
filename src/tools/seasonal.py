"""季节限定 / 节庆 / 网红期窗口（[05] 改进点）。

从 6300 UGC 抽季节性关键词：
- 春：樱/玉兰/海棠/桃/迎春/梨花/踏青
- 夏：荷/莲/夏夜/夜市
- 秋：银杏/红叶/枫/金黄/霜染
- 冬：雪/冰/腊梅/供暖
- 节庆：春节/国庆/中秋/端午/元宵/灯会/庙会/庆典

按 (POI, season) 聚合 (positive_count, negative_count)：
- 关键词 + UGC sentiment=positive → 该 POI 在该季有看点
- 关键词 + UGC sentiment=negative → 该 POI 在该季有警告（暴晒 / 寒风 / 雾霾）

current_month → current_season 映射；
ranking 时如果 POI 当前月份是峰值 → boost；非季节限定 → 不变；当前月份是负向期 → demote。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


# ============================================================
# 关键词库（中文）
# ============================================================

SEASON_KEYWORDS = {
    "spring": ["樱花", "樱", "玉兰", "海棠", "桃花", "迎春", "梨花", "踏青", "春樱", "杏花",
               "赏花", "赏樱", "花期", "春景"],
    "summer": ["荷花", "莲", "夏夜", "夜市", "纳凉", "莲花", "蝉鸣", "荷塘", "纳凉"],
    "autumn": ["银杏", "红叶", "枫", "金黄", "霜染", "秋红", "层林", "赏叶", "赏银杏", "秋景"],
    "winter": ["雪", "冰雪", "冰灯", "腊梅", "梅花", "冰场", "供暖", "雪景", "冬奥"],
    "festival": ["春节", "国庆", "中秋", "端午", "元宵", "灯会", "庙会", "庆典", "花会"],
}

# 反向（"夏季暴晒""冬季寒风" 这类是 POI 在该季的劣势）
NEGATIVE_PHRASES = {
    "summer": ["暴晒", "烈日", "酷热"],
    "winter": ["寒风", "严寒", "刺骨", "雾霾"],
}

# 月份 → 主季节
MONTH_TO_SEASON = {
    1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn", 12: "winter",
}

# 月份 → 节庆
MONTH_FESTIVALS = {
    1: ["春节", "元宵"], 2: ["春节", "元宵"], 5: ["五一"],
    6: ["端午"], 9: ["中秋"], 10: ["国庆"],
    12: ["圣诞", "跨年"],
}


# ============================================================
# 数据类型
# ============================================================

@dataclass
class SeasonalSignal:
    poi_name: str
    spring_pos: int = 0
    summer_pos: int = 0
    autumn_pos: int = 0
    winter_pos: int = 0
    festival_pos: int = 0
    summer_neg: int = 0   # "夏季暴晒" 等劣势计数
    winter_neg: int = 0
    evidence: dict = field(default_factory=dict)  # season → [evidence_summary]

    def peak_seasons(self, threshold: int = 2) -> list[str]:
        """该 POI 的高光季节（positive 计数 ≥ threshold）。"""
        out = []
        for s, n in (("spring", self.spring_pos), ("summer", self.summer_pos),
                     ("autumn", self.autumn_pos), ("winter", self.winter_pos)):
            if n >= threshold:
                out.append(s)
        return out

    def avoid_seasons(self, threshold: int = 2) -> list[str]:
        out = []
        if self.summer_neg >= threshold:
            out.append("summer")
        if self.winter_neg >= threshold:
            out.append("winter")
        return out

    def is_seasonal_poi(self) -> bool:
        """这个 POI 有明显季节倾向吗？"""
        return bool(self.peak_seasons(threshold=2)) or bool(self.avoid_seasons(threshold=2))


# ============================================================
# 索引构建
# ============================================================

_SIGNALS: dict[str, SeasonalSignal] = {}


def build_index(force_rebuild: bool = False) -> int:
    """从 SQLite 读 UGC 建 POI → SeasonalSignal 索引。"""
    global _SIGNALS
    if _SIGNALS and not force_rebuild:
        return len(_SIGNALS)

    from loader import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT poi_name, sentiment, evidence_summary "
        "FROM ugc_aspects "
        "WHERE evidence_summary IS NOT NULL AND poi_name IS NOT NULL"
    ).fetchall()
    conn.close()

    sigs: dict[str, SeasonalSignal] = {}
    for r in rows:
        poi = r["poi_name"]
        sent = r["sentiment"] or "neutral"
        txt = r["evidence_summary"]
        sig = sigs.setdefault(poi, SeasonalSignal(poi_name=poi))

        # 正向季节关键词
        for season, kws in SEASON_KEYWORDS.items():
            for kw in kws:
                if kw not in txt:
                    continue
                if sent == "positive":
                    setattr(sig, f"{season}_pos", getattr(sig, f"{season}_pos") + 1)
                    sig.evidence.setdefault(season, []).append(txt[:100])
                elif sent == "negative" and season in ("summer", "winter"):
                    setattr(sig, f"{season}_neg", getattr(sig, f"{season}_neg") + 1)
                break  # 同一段不多次累加同一季

        # 负向短语（暴晒 / 寒风）专门 boost negative
        for season, phrases in NEGATIVE_PHRASES.items():
            for ph in phrases:
                if ph in txt:
                    setattr(sig, f"{season}_neg", getattr(sig, f"{season}_neg") + 1)
                    break

    _SIGNALS = sigs
    n_seasonal = sum(1 for s in sigs.values() if s.is_seasonal_poi())
    logger.info(f"[seasonal] {len(sigs)} POI 索引，{n_seasonal} 个有显著季节倾向")
    return len(sigs)


# ============================================================
# 查询接口
# ============================================================

def get_signal(poi_name: str) -> Optional[SeasonalSignal]:
    """查询 POI 季节信号 —— 合并所有共享前 3 字的 UGC 信号（同 POI 在不同片区命名下）。"""
    if not _SIGNALS:
        build_index()
    if not poi_name:
        return None

    # 始终用前缀合并（玉渊潭公园 + 玉渊潭-钓鱼台片区 在 UGC 里是同一个事物）
    if len(poi_name) >= 3:
        prefix = poi_name[:3]
        merged = SeasonalSignal(poi_name=poi_name)
        n_merged = 0
        for name, sig in _SIGNALS.items():
            if name.startswith(prefix) or prefix in name[:5] or poi_name in name or name in poi_name:
                for f in ("spring_pos", "summer_pos", "autumn_pos",
                          "winter_pos", "festival_pos", "summer_neg", "winter_neg"):
                    setattr(merged, f, getattr(merged, f) + getattr(sig, f))
                for season, evs in sig.evidence.items():
                    merged.evidence.setdefault(season, []).extend(evs)
                n_merged += 1
        if n_merged > 0:
            return merged

    return _SIGNALS.get(poi_name)


def current_season(today: Optional[date] = None) -> str:
    today = today or date.today()
    return MONTH_TO_SEASON[today.month]


def get_season_match(
    poi_name: str,
    today: Optional[date] = None,
) -> dict:
    """返回 POI 与当前季节的匹配状态。

    Returns:
        {
            "current_season": "spring",
            "current_month": 5,
            "is_peak": bool,    # 该 POI 在当前季有 ≥2 条 positive
            "is_avoid": bool,   # 该 POI 在当前季有 ≥2 条 negative
            "score_adjust": float,  # +0.1 / 0 / -0.2
            "reason": str,
        }
    """
    today = today or date.today()
    season = MONTH_TO_SEASON[today.month]
    sig = get_signal(poi_name)
    out = {
        "current_season": season,
        "current_month": today.month,
        "is_peak": False,
        "is_avoid": False,
        "score_adjust": 0.0,
        "reason": "",
    }
    if sig is None:
        return out

    pos_n = getattr(sig, f"{season}_pos", 0)
    neg_n = getattr(sig, f"{season}_neg", 0)

    if pos_n >= 2:
        out["is_peak"] = True
        out["score_adjust"] = 0.10
        evd = sig.evidence.get(season, [""])[0][:60]
        out["reason"] = f"🌟 {season} 季是 {sig.poi_name} 的网红期（{pos_n} 条 UGC 提及）：{evd}"
    elif neg_n >= 2:
        out["is_avoid"] = True
        out["score_adjust"] = -0.20
        out["reason"] = f"⚠️ {season} 季是 {sig.poi_name} 的劣势期（{neg_n} 条 UGC 提及暴晒/寒风/雾霾等）"
    return out


def get_top_seasonal_pois(season: str, top_k: int = 10) -> list[tuple[str, int]]:
    """某季的 top POI（按 positive 计数倒序）。"""
    if not _SIGNALS:
        build_index()
    pairs = [(sig.poi_name, getattr(sig, f"{season}_pos", 0))
             for sig in _SIGNALS.values()
             if getattr(sig, f"{season}_pos", 0) >= 2]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs[:top_k]


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import time
    t0 = time.time()
    n = build_index()
    print(f"index built: {n} POI in {time.time()-t0:.2f}s")

    # 季节峰值
    print("\n=== 各季 top-5 POI ===")
    for season in ("spring", "summer", "autumn", "winter"):
        top = get_top_seasonal_pois(season, top_k=5)
        print(f"\n[{season}]")
        for name, n in top:
            print(f"  · {name}（{n} 条 UGC）")

    # 单查
    print("\n=== 单 POI 查询（5 月即春末 = spring）===")
    for poi in ["奥林匹克森林公园", "玉渊潭公园", "北海公园", "雍和宫", "故宫博物院"]:
        match = get_season_match(poi, today=date(2026, 5, 21))
        sig = get_signal(poi)
        peaks = sig.peak_seasons() if sig else []
        avoids = sig.avoid_seasons() if sig else []
        print(f"  {poi:20s} 峰={peaks} 避={avoids}")
        print(f"      → 5 月 adjust={match['score_adjust']:+.2f} {match['reason'][:80]}")

    # 4 月查樱花对比 11 月查樱花
    print("\n=== 玉渊潭 4 月 vs 11 月 ===")
    for m in [4, 7, 11]:
        match = get_season_match("玉渊潭公园", today=date(2026, m, 15))
        print(f"  {m} 月: adjust={match['score_adjust']:+.2f} {match['reason'][:80]}")
