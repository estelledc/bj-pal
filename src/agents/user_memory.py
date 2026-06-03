"""v2.7 D6 stateful agent — 跨 session 用户偏好沉淀。

之前 BJ-Pal 是 stateless：每次 plan 都从零开始，用户得每次重说"我减脂""不吃辣"。
v2.7 加 user_memory 层：

- record_preference(user_id, key, value)  写入 / 累计 mention_count
- get_preferences(user_id)                 读全部活跃偏好
- forget(user_id, key)                     用户明确"AI 别记这个"
- infer_from_user_input(user_id, raw)      仅在显式记忆入口中用 LLM 抽取并沉淀偏好
- merge_into_prompt(base, user_id)         给 planner 注入"用户长期偏好"段落

设计原则：
- 用户拥有所有数据（forget 必须有效）
- 偏好分级：confidence 表示抽取可靠度；mention_count 仅作内部累计，不直接展示
- 偏好衰减：30 天没复现的 confidence × 0.5
- 复用现有 SQLite（tool_calls.db），新增 user_memory 表
"""

from __future__ import annotations

import json
import re
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


def _normalize_memory_tag(tag: object) -> str:
    text = str(tag or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[\s\-\\/]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def _diet_memory_kind(tag: str) -> str:
    if tag.startswith(("no_", "avoid_")):
        return "dislike"
    if any(marker in tag for marker in ("allergy", "intoler", "taboo", "forbidden", "过敏", "忌口")):
        return "dislike"
    return "preference"


def _record_intake_memory(user_id: str, intake) -> list[MemoryEntry]:
    written: list[MemoryEntry] = []

    def _record(kind: str, key_prefix: str, tag: object,
                *, confidence: float = 0.72, value: object = True) -> None:
        normalized = _normalize_memory_tag(tag)
        if not normalized:
            return
        written.append(record_preference(
            user_id,
            key=f"{key_prefix}:{normalized}",
            value=value,
            kind=kind,
            confidence=confidence,
        ))

    for tag in getattr(intake, "diet_flags", []) or []:
        normalized = _normalize_memory_tag(tag)
        if not normalized:
            continue
        written.append(record_preference(
            user_id,
            key=f"diet:{normalized}",
            value=True,
            kind=_diet_memory_kind(normalized),
            confidence=0.82,
        ))
    for tag in getattr(intake, "taste_tags", []) or []:
        _record("preference", "taste", tag, confidence=0.68)
    for tag in getattr(intake, "preference_tags", []) or []:
        _record("preference", "preference", tag, confidence=0.72)
    for tag in getattr(intake, "scene_tags", []) or []:
        _record("preference", "scene", tag, confidence=0.62)
    for tag in getattr(intake, "avoid_tags", []) or []:
        _record("dislike", "avoid", tag, confidence=0.78)
    for tag in getattr(intake, "risk_tags", []) or []:
        _record("dislike", "risk", tag, confidence=0.68)

    return written


def infer_from_user_input(
    user_id: str,
    raw: str,
    *,
    client=None,
    use_llm: bool = True,
) -> list[MemoryEntry]:
    """从 query 用 LLM 抽取并沉淀偏好。返回新写入的条目。

    记忆沉淀不做关键词规则兜底：LLM 没抽到就不写，避免把上下文词误当长期偏好。
    """
    if not raw or not user_id:
        return []

    written: list[MemoryEntry] = []

    if not use_llm:
        return []
    try:
        from agents.text_intake import extract_from_text
        intake = extract_from_text(
            raw,
            client=client,
            use_llm=True,
            fallback_to_rules=False,
        )
        written.extend(_record_intake_memory(user_id, intake))
    except Exception:
        return []

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
    from agents.llm_client import LLMResponse

    class _SelfTestMemoryClient:
        @property
        def name(self):
            return "user-memory-self-test"

        def complete(self, *args, **kwargs):
            parsed = {
                "area_anchor": "",
                "poi_name": "",
                "taste_tags": ["coffee"],
                "scene_tags": ["kid_friendly"],
                "risk_tags": [],
                "diet_flags": ["light_diet", "no_spicy"],
                "preference_tags": [],
                "avoid_tags": [],
                "aspects": [],
            }
            return LLMResponse(text="{}", parsed=parsed)

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
        client=_SelfTestMemoryClient(),
    )
    print(f"✓ Case 6 infer: {len(written)} 条")
    keys = [w.mem_key for w in written]
    kinds = {w.mem_key: w.kind for w in written}
    assert any("light_diet" in k for k in keys)
    assert any("no_spicy" in k for k in keys)
    assert any("kid_friendly" in k for k in keys)
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
