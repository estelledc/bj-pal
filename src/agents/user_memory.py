"""v2.7 D6 stateful agent — 跨 session 用户偏好沉淀。

之前 BJ-Pal 是 stateless：每次 plan 都从零开始，用户得每次重说"我减脂""不吃辣"。
v2.7 加 user_memory 层：

- record_preference(user_id, key, value)  写入 / 累计 mention_count
- get_preferences(user_id)                 读全部活跃偏好
- forget(user_id, key)                     用户明确"AI 别记这个"
- infer_from_user_input(user_id, raw)      从 query 自动抽取并沉淀偏好
- merge_into_prompt(base, user_id)         给 planner 注入"用户长期偏好"段落

设计原则：
- 用户拥有所有数据（forget 必须有效）
- 偏好分级：mention_count 越高 confidence 越大
- 偏好衰减：30 天没复现的 confidence × 0.5
- 复用现有 SQLite（tool_calls.db），新增 user_memory 表
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = ROOT / "tool_calls.db"
_LOCK = threading.Lock()

DECAY_DAYS = 30
DECAY_FACTOR = 0.5
DEFAULT_CONFIDENCE = 0.7


# ============================================================
# Schema
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_memory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- preference | fact | dislike | identity
    mem_key         TEXT NOT NULL,          -- e.g. "diet", "favorite_area"
    mem_value       TEXT NOT NULL,          -- JSON-encoded value
    confidence      REAL NOT NULL DEFAULT 0.7,
    mention_count   INTEGER NOT NULL DEFAULT 1,
    first_seen_at   REAL NOT NULL,
    last_seen_at    REAL NOT NULL,
    forgotten       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, kind, mem_key)
);
CREATE INDEX IF NOT EXISTS idx_user_memory_user ON user_memory(user_id, forgotten);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema():
    with _LOCK, closing(_conn()) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


_ensure_schema()


# ============================================================
# 数据结构
# ============================================================

@dataclass
class MemoryEntry:
    user_id: str
    kind: str
    mem_key: str
    mem_value: object   # JSON 可序列化
    confidence: float
    mention_count: int
    first_seen_at: float
    last_seen_at: float

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 核心 API
# ============================================================

def record_preference(
    user_id: str,
    key: str,
    value: object,
    *,
    kind: str = "preference",
    confidence: Optional[float] = None,
) -> MemoryEntry:
    """写入 / 累计偏好。

    已存在 (user_id, kind, key) 时：mention_count += 1，confidence 取 max，
    last_seen_at 刷新；value 用最新值（用户偏好可能演化）。

    Args:
        kind: preference | dislike | fact | identity
    """
    if not user_id or not key:
        raise ValueError("user_id 和 key 不能为空")
    val_json = json.dumps(value, ensure_ascii=False)
    now = time.time()
    conf = confidence if confidence is not None else DEFAULT_CONFIDENCE

    with _LOCK, closing(_conn()) as conn:
        existing = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND kind=? AND mem_key=?",
            (user_id, kind, key),
        ).fetchone()

        if existing is None:
            conn.execute(
                "INSERT INTO user_memory(user_id, kind, mem_key, mem_value, confidence, "
                "mention_count, first_seen_at, last_seen_at, forgotten) "
                "VALUES (?,?,?,?,?,?,?,?,0)",
                (user_id, kind, key, val_json, conf, 1, now, now),
            )
        else:
            new_mc = (existing["mention_count"] or 0) + 1
            new_conf = max(existing["confidence"] or 0.0, conf)
            # 复活已被 forgotten 的条目（用户重新提到了）
            conn.execute(
                "UPDATE user_memory SET mem_value=?, confidence=?, mention_count=?, "
                "last_seen_at=?, forgotten=0 "
                "WHERE id=?",
                (val_json, new_conf, new_mc, now, existing["id"]),
            )
        conn.commit()

    return MemoryEntry(
        user_id=user_id, kind=kind, mem_key=key, mem_value=value,
        confidence=conf, mention_count=1,
        first_seen_at=now, last_seen_at=now,
    )


def get_preferences(
    user_id: str,
    *,
    include_forgotten: bool = False,
    apply_decay: bool = True,
) -> list[MemoryEntry]:
    """读某用户全部活跃偏好（按 last_seen_at 倒序）。"""
    if not user_id:
        return []
    where = "WHERE user_id=?"
    if not include_forgotten:
        where += " AND forgotten=0"
    with _LOCK, closing(_conn()) as conn:
        rows = conn.execute(
            f"SELECT * FROM user_memory {where} ORDER BY last_seen_at DESC",
            (user_id,),
        ).fetchall()

    out: list[MemoryEntry] = []
    now = time.time()
    for r in rows:
        conf = r["confidence"]
        if apply_decay:
            age_days = (now - r["last_seen_at"]) / 86400
            if age_days > DECAY_DAYS:
                conf = round(conf * DECAY_FACTOR, 3)
        out.append(MemoryEntry(
            user_id=r["user_id"], kind=r["kind"], mem_key=r["mem_key"],
            mem_value=json.loads(r["mem_value"]),
            confidence=conf, mention_count=r["mention_count"],
            first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"],
        ))
    return out


def forget(user_id: str, key: str, kind: str = "preference") -> bool:
    """用户明确"AI 别记这个" → 标记 forgotten=1。返回是否找到记录。"""
    with _LOCK, closing(_conn()) as conn:
        cur = conn.execute(
            "UPDATE user_memory SET forgotten=1 WHERE user_id=? AND kind=? AND mem_key=?",
            (user_id, kind, key),
        )
        conn.commit()
        return cur.rowcount > 0


def forget_all(user_id: str) -> int:
    """清空某用户所有 memory。返回影响行数。"""
    with _LOCK, closing(_conn()) as conn:
        cur = conn.execute(
            "UPDATE user_memory SET forgotten=1 WHERE user_id=? AND forgotten=0",
            (user_id,),
        )
        conn.commit()
        return cur.rowcount


# ============================================================
# 自动抽取：复用 text_intake 关键词字典
# ============================================================

DIET_PATTERNS: list[tuple[str, list[str]]] = [
    ("light_diet", ["减脂", "轻食", "低油", "清淡", "少油"]),
    ("no_spicy", ["不吃辣", "不能吃辣", "怕辣", "受不了辣"]),
    ("vegetarian", ["素食", "吃素", "纯素"]),
    ("no_seafood", ["不吃海鲜", "海鲜过敏"]),
    ("no_pork", ["不吃猪肉", "回民", "穆斯林"]),
]

PARTY_PATTERNS: list[tuple[str, list[str]]] = [
    ("with_child", ["带娃", "带孩子", "带 5 岁", "带小朋友", "亲子"]),
    ("with_elderly", ["带老人", "父母", "长辈", "爹妈"]),
    ("couple", ["和老婆", "和女朋友", "夫妻", "情侣"]),
]


def infer_from_user_input(user_id: str, raw: str) -> list[MemoryEntry]:
    """从 query 自动抽取并沉淀偏好。返回新写入的条目。"""
    if not raw or not user_id:
        return []

    written: list[MemoryEntry] = []

    for diet_key, kws in DIET_PATTERNS:
        if any(kw in raw for kw in kws):
            written.append(record_preference(
                user_id, key=f"diet:{diet_key}", value=True,
                kind="preference", confidence=0.75,
            ))

    for party_key, kws in PARTY_PATTERNS:
        if any(kw in raw for kw in kws):
            written.append(record_preference(
                user_id, key=f"party:{party_key}", value=True,
                kind="preference", confidence=0.75,
            ))

    # 复用 text_intake 关键词字典（taste / scene / risk）
    # 否定上下文：keyword 前 6 字内出现否定词 → 不抽 positive，转 dislike
    NEG_CTX = ["不", "无", "别", "不要", "避开", "受不了", "怕", "不能"]

    def _is_negated(kw: str, text: str) -> bool:
        idx = text.find(kw)
        if idx < 0:
            return False
        prefix = text[max(0, idx - 6):idx]
        return any(neg in prefix for neg in NEG_CTX)

    try:
        from agents.text_intake import (
            RISK_KEYWORDS,
            SCENE_KEYWORDS,
            TASTE_KEYWORDS,
        )
        for tag, kws in TASTE_KEYWORDS.items():
            hit_kw = next((kw for kw in kws
                            if kw in raw or kw.lower() in raw.lower()), None)
            if hit_kw is None:
                continue
            if _is_negated(hit_kw, raw):
                # 否定语义 → 抽到 dislike
                written.append(record_preference(
                    user_id, key=f"taste:{tag}", value=False,
                    kind="dislike", confidence=0.70,
                ))
            else:
                written.append(record_preference(
                    user_id, key=f"taste:{tag}", value=True,
                    confidence=0.65,
                ))
        for tag, kws in SCENE_KEYWORDS.items():
            if any(kw in raw for kw in kws):
                written.append(record_preference(
                    user_id, key=f"scene:{tag}", value=True,
                    confidence=0.60,
                ))
        for tag, kws in RISK_KEYWORDS.items():
            if any(kw in raw for kw in kws):
                written.append(record_preference(
                    user_id, key=f"risk:{tag}", value=True,
                    kind="dislike", confidence=0.65,
                ))
    except ImportError:
        pass

    return written


# ============================================================
# 给 planner 注入 prompt
# ============================================================

def merge_into_prompt(
    base_input: str,
    user_id: str,
    *,
    confidence_threshold: float = 0.4,
    max_lines: int = 8,
) -> str:
    """读 user_memory → 拼"用户长期偏好"段落 → 接到 base_input 后。

    confidence_threshold: 衰减后置信度低于此值的不注入（避免噪声）
    max_lines: 最多列几条偏好（防 token 膨胀）
    """
    if not user_id:
        return base_input

    prefs = get_preferences(user_id, apply_decay=True)
    relevant = [p for p in prefs if p.confidence >= confidence_threshold]
    if not relevant:
        return base_input

    # 按 confidence 降序，取前 N
    relevant.sort(key=lambda p: (p.confidence, p.mention_count), reverse=True)
    relevant = relevant[:max_lines]

    lines = ["", "[用户长期偏好（来自 AI 记忆）]"]
    for p in relevant:
        marker = "✓" if p.kind == "preference" else "✗" if p.kind == "dislike" else "·"
        lines.append(
            f"- {marker} {p.mem_key} (提及 {p.mention_count}次, 置信 {p.confidence:.2f})"
        )
    return base_input + "\n".join(lines) if base_input else "\n".join(lines[1:])


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import uuid
    uid = f"u-{uuid.uuid4().hex[:8]}"

    # Case 1: record + get
    e = record_preference(uid, "diet:light_diet", True)
    prefs = get_preferences(uid)
    assert len(prefs) == 1
    assert prefs[0].mem_key == "diet:light_diet"
    print(f"✓ Case 1 record+get: 1 条偏好")

    # Case 2: 重复 record → mention_count 累计
    record_preference(uid, "diet:light_diet", True)
    record_preference(uid, "diet:light_diet", True)
    prefs = get_preferences(uid)
    assert prefs[0].mention_count == 3
    print(f"✓ Case 2 累计: mention_count={prefs[0].mention_count}")

    # Case 3: 不同 key
    record_preference(uid, "party:with_child", True)
    record_preference(uid, "taste:coffee", True)
    prefs = get_preferences(uid)
    assert len(prefs) == 3
    print(f"✓ Case 3 多 key: {[p.mem_key for p in prefs]}")

    # Case 4: forget
    ok = forget(uid, "taste:coffee")
    assert ok
    prefs = get_preferences(uid)
    assert len(prefs) == 2
    assert all(p.mem_key != "taste:coffee" for p in prefs)
    print(f"✓ Case 4 forget: {[p.mem_key for p in prefs]}")

    # Case 5: 复活 forgotten
    record_preference(uid, "taste:coffee", True)
    prefs = get_preferences(uid)
    assert len(prefs) == 3
    print(f"✓ Case 5 复活 forgotten: 重新加回 taste:coffee")

    # Case 6: infer_from_user_input
    uid2 = f"u-{uuid.uuid4().hex[:8]}"
    written = infer_from_user_input(
        uid2,
        "今天下午带 5 岁娃出去玩，老婆减脂不吃辣，想找个咖啡店",
    )
    print(f"✓ Case 6 infer: {len(written)} 条")
    keys = [w.mem_key for w in written]
    kinds = {w.mem_key: w.kind for w in written}
    assert any("light_diet" in k for k in keys)
    assert any("no_spicy" in k for k in keys)
    assert any("with_child" in k for k in keys)
    assert any("coffee" in k for k in keys)
    # taste:spicy 应被否定上下文识别为 dislike，不应是 preference
    if "taste:spicy" in kinds:
        assert kinds["taste:spicy"] == "dislike", \
            f"'不吃辣' 应抽到 dislike，实际 {kinds['taste:spicy']}"

    # Case 7: merge_into_prompt
    merged = merge_into_prompt("4 人下午", uid2)
    print(f"\n✓ Case 7 merged:\n{merged}")
    assert "long term" not in merged.lower()  # 中文段落
    assert "用户长期偏好" in merged

    # Case 8: forget_all
    n = forget_all(uid2)
    assert n > 0
    prefs = get_preferences(uid2)
    assert len(prefs) == 0
    print(f"✓ Case 8 forget_all: 清空 {n} 条")

    print("\n所有 user_memory 自测通过！")
