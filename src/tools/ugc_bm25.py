"""UGC BM25 检索（[28] Hybrid retrieval 第一段）。

把 6300 条 UGC 的 evidence_summary 用 jieba 分词后建 BM25Okapi 索引，
提供基于 query 的 top-k 检索。

为什么不直接 SQL LIKE？
- LIKE 只能精确字面匹配，"等位时间长" 匹配不到 "排队 1 小时"
- BM25 基于 TF-IDF 加权，对长文档罚分、对稀有词奖励
- 中文分词后能命中近义表达（"咖啡馆/咖啡店"，"安静/不吵"）

为什么不直接 dense embedding？
- BM25 0 依赖 GPU + 启动 1 秒（vs dense 30 秒下载模型）
- 中文场景下 BM25 + 关键词匹配 vs dense 的相对差距 < 5%（[19] hybrid 后再补）

后续 [21] HyDE 在此基础上加 dense 这一路，做混合召回。
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

# 全局索引（懒加载，进程内单例）
_BM25_INDEX = None
_BM25_DOCS: list[dict] = []
_INDEX_LOCK = threading.Lock()


@dataclass
class BM25Hit:
    """单条命中记录。"""
    record_id: str
    poi_name: str
    area_anchor: str
    aspect_type: str
    sentiment: str
    confidence: float
    evidence_summary: str
    score: float
    weekend_afternoon_intensity: Optional[float] = None


def _tokenize(text: str) -> list[str]:
    """中文分词 — jieba。

    过滤掉单字虚词（的/了/吗）和纯空白；保留长度 ≥ 2 或英文字母数字。
    """
    if not text:
        return []
    import jieba
    stopwords = {"的", "了", "吗", "和", "在", "是", "有", "也", "都", "就",
                 "很", "去", "可", "为", "对", "以", "它", "我", "你", "他",
                 "其", "于", "或", "等", "中", "上", "下", "里", "得", "把",
                 "及", "但", "却", "并", "而", "且", "因", "所", "之", "出"}
    return [
        t for t in jieba.cut(text.lower())
        if t.strip() and (len(t) >= 2 or t.isalnum()) and t not in stopwords
    ]


def build_index(force_rebuild: bool = False) -> int:
    """从 SQLite 读 6300 UGC 建 BM25 索引（进程内单例）。

    Returns:
        索引文档数
    """
    global _BM25_INDEX, _BM25_DOCS
    if _BM25_INDEX is not None and not force_rebuild:
        return len(_BM25_DOCS)

    with _INDEX_LOCK:
        if _BM25_INDEX is not None and not force_rebuild:
            return len(_BM25_DOCS)

        from rank_bm25 import BM25Okapi
        from loader import get_conn

        conn = get_conn()
        rows = conn.execute(
            """
            SELECT record_id, area_anchor, poi_name, aspect_type, sentiment,
                   confidence, evidence_summary, weekend_afternoon_intensity
            FROM ugc_aspects
            WHERE evidence_summary IS NOT NULL AND evidence_summary != ''
            """
        ).fetchall()
        conn.close()

        docs = []
        tokenized = []
        for r in rows:
            ev = r["evidence_summary"]
            # 把 poi_name 和 aspect_type 也拼进去当字面 boost
            full = f"{ev} {r['poi_name'] or ''} {r['aspect_type'] or ''}"
            tokens = _tokenize(full)
            if not tokens:
                continue
            docs.append(dict(r))
            tokenized.append(tokens)

        _BM25_INDEX = BM25Okapi(tokenized)
        _BM25_DOCS = docs
        logger.info(
            "[ugc_bm25] indexed %s UGC docs, avg_doc_len=%.1f",
            len(docs),
            _BM25_INDEX.avgdl,
        )
        return len(docs)


def search(
    query: str,
    top_k: int = 20,
    area_anchor: Optional[str] = None,
    sentiment: Optional[str] = None,
    min_confidence: float = 0.0,
    boost_weekend_afternoon: bool = True,
) -> list[BM25Hit]:
    """BM25 查询。

    Args:
        query: 自然语言查询（"出片好看的安静咖啡馆"）
        top_k: 返回 top 多少条
        area_anchor: 限制片区
        sentiment: 限制情感（positive/negative/neutral）
        min_confidence: 最低置信度
        boost_weekend_afternoon: True 时 weekend_afternoon_intensity ≥ 0.7 的文档分数 ×1.3

    Returns:
        [BM25Hit] 按分数降序
    """
    if _BM25_INDEX is None:
        build_index()

    tokens = _tokenize(query)
    if not tokens:
        return []

    raw_scores = _BM25_INDEX.get_scores(tokens)

    hits: list[BM25Hit] = []
    for doc, raw_score in zip(_BM25_DOCS, raw_scores):
        if raw_score <= 0:
            continue
        if area_anchor and doc["area_anchor"] != area_anchor:
            continue
        if sentiment and doc["sentiment"] != sentiment:
            continue
        if (doc["confidence"] or 0) < min_confidence:
            continue

        # 时段加权
        score = raw_score
        intensity = doc.get("weekend_afternoon_intensity")
        if boost_weekend_afternoon and intensity and intensity >= 0.7:
            score *= 1.3

        hits.append(BM25Hit(
            record_id=doc["record_id"],
            poi_name=doc["poi_name"] or "",
            area_anchor=doc["area_anchor"] or "",
            aspect_type=doc["aspect_type"] or "",
            sentiment=doc["sentiment"] or "",
            confidence=doc["confidence"] or 0.0,
            evidence_summary=doc["evidence_summary"] or "",
            score=round(float(score), 3),
            weekend_afternoon_intensity=intensity,
        ))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


# ============================================================
# CLI / 自测
# ============================================================

if __name__ == "__main__":
    import time
    t0 = time.time()
    n = build_index()
    print(f"index built: {n} docs in {time.time()-t0:.2f}s")

    queries = [
        "周末排队太久",
        "出片好看的安静咖啡馆",
        "适合带 5 岁娃",
        "雍和宫附近吃饭",
        "下午茶低糖减脂",
    ]
    for q in queries:
        t0 = time.time()
        hits = search(q, top_k=5, boost_weekend_afternoon=True)
        print(f"\nQ: {q}  ({(time.time()-t0)*1000:.1f}ms, {len(hits)} hits)")
        for h in hits[:3]:
            inten = f" int={h.weekend_afternoon_intensity:.1f}" if h.weekend_afternoon_intensity else ""
            print(f"  [{h.score:.2f}] {h.poi_name} ({h.area_anchor}) "
                  f"{h.sentiment}{inten}")
            print(f"      {h.evidence_summary[:80]}")
