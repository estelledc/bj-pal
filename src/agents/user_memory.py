"""User-owned long-term memory with explicit lifecycle and conflict semantics.

Memory writes are never part of normal planning. The UI's dedicated memory
intake is the only current write path; the planner only reads confirmed,
non-expired entries. Existing v2.7 callers keep using ``record_preference`` and
``get_preferences`` while v4.5 exposes the richer state machine underneath.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.user_memory import (
    LEGACY_SHARED_DB,
    USER_MEMORY_DB_ENV,
    USER_MEMORY_SCHEMA,
    ensure_user_memory_metadata,
    resolve_user_memory_path,
)

_DB_PATH: Path | None = None
_LOCK = threading.Lock()

DECAY_DAYS = 30
DECAY_FACTOR = 0.5
DEFAULT_CONFIDENCE = 0.7

MemoryKind = Literal["preference", "dislike", "fact", "identity"]
MemorySource = Literal[
    "manual_entry",
    "explicit_user_input",
    "inferred",
    "imported",
    "legacy",
]
MemoryAction = Literal["created", "reinforced", "replaced", "conflict_rejected"]

VALID_KINDS = frozenset({"preference", "dislike", "fact", "identity"})
VALID_SOURCES = frozenset(
    {"manual_entry", "explicit_user_input", "inferred", "imported", "legacy"}
)


_SCHEMA = USER_MEMORY_SCHEMA

_COLUMN_MIGRATIONS = {
    "source": "TEXT NOT NULL DEFAULT 'legacy'",
    "confirmed_at": "REAL",
    "expires_at": "REAL",
    "revision": "INTEGER NOT NULL DEFAULT 1",
}


def database_path() -> Path:
    """Resolve explicit override, verified dedicated store, or legacy compatibility."""
    return resolve_user_memory_path(_DB_PATH)


def _conn() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema() -> None:
    """Create the v4.5 schema and add columns to existing v2.7 databases."""
    path = database_path()
    with _LOCK:
        with closing(_conn()) as conn:
            conn.executescript(_SCHEMA)
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(user_memory)").fetchall()
            }
            for name, definition in _COLUMN_MIGRATIONS.items():
                if name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE user_memory ADD COLUMN {name} {definition}"
                    )
            conn.commit()
        ensure_user_memory_metadata(path)


_ensure_schema()


@dataclass(frozen=True)
class MemoryEntry:
    user_id: str
    kind: str
    mem_key: str
    mem_value: object
    confidence: float
    mention_count: int
    first_seen_at: float
    last_seen_at: float
    source: str = "legacy"
    confirmed_at: Optional[float] = None
    expires_at: Optional[float] = None
    revision: int = 1
    forgotten: bool = False
    expired: bool = False

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at is not None

    @property
    def active(self) -> bool:
        return not self.forgotten and not self.expired

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MemoryWriteResult:
    action: MemoryAction
    entry: MemoryEntry
    incoming_value_sha256: str
    previous_value_sha256: Optional[str] = None


@dataclass(frozen=True)
class MemoryEvent:
    event_id: int
    user_id: str
    kind: str
    mem_key: str
    event_type: str
    revision: int
    source: str
    value_sha256: Optional[str]
    previous_value_sha256: Optional[str]
    reason: str
    created_at: float


def upsert_memory(
    user_id: str,
    key: str,
    value: object,
    *,
    kind: MemoryKind | str = "preference",
    confidence: Optional[float] = None,
    source: MemorySource | str = "manual_entry",
    confirmed: bool = True,
    expires_at: Optional[float] = None,
) -> MemoryWriteResult:
    """Apply deterministic create/reinforce/replace/conflict semantics.

    * Same value reinforces mention count and keeps the strongest confidence.
    * A confirmed different value starts a new revision and resets mentions.
    * An unconfirmed different value never overwrites an active memory; it is
      recorded as a hash-only conflict for later user resolution.
    * Forgotten or expired state may be replaced without confirmation because
      it is no longer eligible for planning.
    """
    normalized_user = str(user_id or "").strip()
    normalized_key = str(key or "").strip()
    if not normalized_user or not normalized_key:
        raise ValueError("user_id and key must be non-empty")
    if kind not in VALID_KINDS:
        raise ValueError(f"unsupported memory kind: {kind}")
    if source not in VALID_SOURCES:
        raise ValueError(f"unsupported memory source: {source}")

    conf = DEFAULT_CONFIDENCE if confidence is None else float(confidence)
    if not 0.0 <= conf <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if expires_at is not None:
        expires_at = float(expires_at)

    value_json = _canonical_value(value)
    value_hash = _value_sha256(value_json)
    now = time.time()

    with _LOCK, closing(_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND kind=? AND mem_key=?",
            (normalized_user, kind, normalized_key),
        ).fetchone()

        previous_hash: Optional[str] = None
        if existing is None:
            action: MemoryAction = "created"
            confirmed_at = now if confirmed else None
            conn.execute(
                """
                INSERT INTO user_memory(
                    user_id, kind, mem_key, mem_value, confidence, mention_count,
                    first_seen_at, last_seen_at, forgotten, source, confirmed_at,
                    expires_at, revision
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?, 1)
                """,
                (
                    normalized_user,
                    kind,
                    normalized_key,
                    value_json,
                    conf,
                    now,
                    now,
                    source,
                    confirmed_at,
                    expires_at,
                ),
            )
            revision = 1
            reason = "new_memory"
        else:
            previous_hash = _value_sha256(existing["mem_value"])
            existing_expired = (
                existing["expires_at"] is not None and float(existing["expires_at"]) <= now
            )
            same_value = existing["mem_value"] == value_json
            if same_value and not existing_expired:
                action = "reinforced"
                revision = int(existing["revision"] or 1)
                confirmed_at = existing["confirmed_at"] or (now if confirmed else None)
                chosen_source = source if confirmed else existing["source"]
                conn.execute(
                    """
                    UPDATE user_memory
                    SET confidence=?, mention_count=?, last_seen_at=?, forgotten=0,
                        source=?, confirmed_at=?, expires_at=?
                    WHERE id=?
                    """,
                    (
                        max(float(existing["confidence"]), conf),
                        int(existing["mention_count"] or 0) + 1,
                        now,
                        chosen_source,
                        confirmed_at,
                        expires_at if expires_at is not None else existing["expires_at"],
                        existing["id"],
                    ),
                )
                reason = "same_value"
            elif confirmed or bool(existing["forgotten"]) or existing_expired:
                action = "replaced"
                revision = int(existing["revision"] or 1) + 1
                confirmed_at = now if confirmed else None
                conn.execute(
                    """
                    UPDATE user_memory
                    SET mem_value=?, confidence=?, mention_count=1, first_seen_at=?,
                        last_seen_at=?, forgotten=0, source=?, confirmed_at=?,
                        expires_at=?, revision=?
                    WHERE id=?
                    """,
                    (
                        value_json,
                        conf,
                        now,
                        now,
                        source,
                        confirmed_at,
                        expires_at,
                        revision,
                        existing["id"],
                    ),
                )
                reason = "explicit_replacement" if confirmed else "inactive_replacement"
            else:
                action = "conflict_rejected"
                revision = int(existing["revision"] or 1)
                reason = "unconfirmed_value_conflicts_with_active_memory"

        _append_event(
            conn,
            user_id=normalized_user,
            kind=str(kind),
            key=normalized_key,
            event_type=action,
            revision=revision,
            source=str(source),
            value_sha256=value_hash,
            previous_value_sha256=previous_hash,
            reason=reason,
            created_at=now,
        )
        row = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND kind=? AND mem_key=?",
            (normalized_user, kind, normalized_key),
        ).fetchone()
        conn.commit()

    return MemoryWriteResult(
        action=action,
        entry=_entry_from_row(row, now=now, apply_decay=False),
        incoming_value_sha256=value_hash,
        previous_value_sha256=previous_hash,
    )


def record_preference(
    user_id: str,
    key: str,
    value: object,
    *,
    kind: MemoryKind | str = "preference",
    confidence: Optional[float] = None,
    source: MemorySource | str = "manual_entry",
    confirmed: bool = True,
    expires_at: Optional[float] = None,
) -> MemoryEntry:
    """Backward-compatible wrapper returning the current persisted entry."""
    return upsert_memory(
        user_id,
        key,
        value,
        kind=kind,
        confidence=confidence,
        source=source,
        confirmed=confirmed,
        expires_at=expires_at,
    ).entry


def get_preferences(
    user_id: str,
    *,
    include_forgotten: bool = False,
    include_expired: bool = False,
    confirmed_only: bool = False,
    apply_decay: bool = True,
) -> list[MemoryEntry]:
    """Read memory state ordered by recency with lifecycle filters."""
    if not user_id:
        return []
    where = "WHERE user_id=?"
    if not include_forgotten:
        where += " AND forgotten=0"
    if confirmed_only:
        where += " AND confirmed_at IS NOT NULL"
    with _LOCK, closing(_conn()) as conn:
        rows = conn.execute(
            f"SELECT * FROM user_memory {where} ORDER BY last_seen_at DESC",
            (user_id,),
        ).fetchall()

    now = time.time()
    entries = [_entry_from_row(row, now=now, apply_decay=apply_decay) for row in rows]
    if not include_expired:
        entries = [entry for entry in entries if not entry.expired]
    return entries


def confirm_memory(user_id: str, key: str, kind: str = "preference") -> bool:
    """Confirm an existing active candidate so it becomes planner-eligible."""
    now = time.time()
    with _LOCK, closing(_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND kind=? AND mem_key=?",
            (user_id, kind, key),
        ).fetchone()
        if row is None or row["forgotten"]:
            return False
        if row["expires_at"] is not None and float(row["expires_at"]) <= now:
            return False
        conn.execute(
            "UPDATE user_memory SET confirmed_at=?, last_seen_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        _append_event(
            conn,
            user_id=user_id,
            kind=kind,
            key=key,
            event_type="confirmed",
            revision=int(row["revision"] or 1),
            source=row["source"],
            value_sha256=_value_sha256(row["mem_value"]),
            previous_value_sha256=None,
            reason="user_confirmation",
            created_at=now,
        )
        conn.commit()
        return True


def forget(user_id: str, key: str, kind: str = "preference") -> bool:
    """Soft-forget one memory while retaining a reversible local tombstone."""
    now = time.time()
    with _LOCK, closing(_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND kind=? AND mem_key=?",
            (user_id, kind, key),
        ).fetchone()
        if row is None:
            return False
        if not row["forgotten"]:
            conn.execute("UPDATE user_memory SET forgotten=1 WHERE id=?", (row["id"],))
            _append_event(
                conn,
                user_id=user_id,
                kind=kind,
                key=key,
                event_type="forgotten",
                revision=int(row["revision"] or 1),
                source=row["source"],
                value_sha256=_value_sha256(row["mem_value"]),
                previous_value_sha256=None,
                reason="user_soft_forget",
                created_at=now,
            )
            conn.commit()
        return True


def forget_all(user_id: str) -> int:
    """Soft-forget all active memories for compatibility with historical UI/tests."""
    now = time.time()
    with _LOCK, closing(_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM user_memory WHERE user_id=? AND forgotten=0",
            (user_id,),
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE user_memory SET forgotten=1 WHERE id=?", (row["id"],))
            _append_event(
                conn,
                user_id=user_id,
                kind=row["kind"],
                key=row["mem_key"],
                event_type="forgotten",
                revision=int(row["revision"] or 1),
                source=row["source"],
                value_sha256=_value_sha256(row["mem_value"]),
                previous_value_sha256=None,
                reason="user_soft_forget_all",
                created_at=now,
            )
        conn.commit()
        return len(rows)


def delete_memory(user_id: str, key: str, kind: str = "preference") -> bool:
    """Hard-delete state and its audit hashes for a single memory key."""
    with _LOCK, closing(_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM user_memory WHERE user_id=? AND kind=? AND mem_key=?",
            (user_id, kind, key),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "DELETE FROM user_memory_events WHERE user_id=? AND kind=? AND mem_key=?",
            (user_id, kind, key),
        )
        conn.execute("DELETE FROM user_memory WHERE id=?", (row["id"],))
        conn.commit()
        return True


def delete_all(user_id: str) -> int:
    """Hard-delete all state and audit hashes for a user."""
    with _LOCK, closing(_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM user_memory WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        )
        conn.execute("DELETE FROM user_memory_events WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_memory WHERE user_id=?", (user_id,))
        conn.commit()
        return count


def list_memory_events(
    user_id: str,
    *,
    after_event_id: int = 0,
    limit: int = 100,
    kind: Optional[str] = None,
    key: Optional[str] = None,
) -> tuple[MemoryEvent, ...]:
    if after_event_id < 0:
        raise ValueError("after_event_id must be non-negative")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    where = ["user_id=?", "event_id>?"]
    params: list[object] = [user_id, after_event_id]
    if kind is not None:
        where.append("kind=?")
        params.append(kind)
    if key is not None:
        where.append("mem_key=?")
        params.append(key)
    params.append(limit)
    with _LOCK, closing(_conn()) as conn:
        rows = conn.execute(
            f"SELECT * FROM user_memory_events WHERE {' AND '.join(where)} "
            "ORDER BY event_id LIMIT ?",
            params,
        ).fetchall()
    return tuple(_event_from_row(row) for row in rows)


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


def _record_intake_memory(
    user_id: str,
    intake,
    *,
    source: MemorySource | str,
    confirmed: bool,
) -> list[MemoryEntry]:
    written: list[MemoryEntry] = []

    def _record(
        kind: str,
        key_prefix: str,
        tag: object,
        *,
        confidence: float = 0.72,
        value: object = True,
    ) -> None:
        normalized = _normalize_memory_tag(tag)
        if not normalized:
            return
        written.append(
            record_preference(
                user_id,
                key=f"{key_prefix}:{normalized}",
                value=value,
                kind=kind,
                confidence=confidence,
                source=source,
                confirmed=confirmed,
            )
        )

    for tag in getattr(intake, "diet_flags", []) or []:
        normalized = _normalize_memory_tag(tag)
        if not normalized:
            continue
        written.append(
            record_preference(
                user_id,
                key=f"diet:{normalized}",
                value=True,
                kind=_diet_memory_kind(normalized),
                confidence=0.82,
                source=source,
                confirmed=confirmed,
            )
        )
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
    source: MemorySource | str = "explicit_user_input",
    confirmed: bool = True,
) -> list[MemoryEntry]:
    """Extract memory only from the explicit memory-intake path."""
    if not raw or not user_id or not use_llm:
        return []
    try:
        from agents.text_intake import extract_from_text

        intake = extract_from_text(
            raw,
            client=client,
            use_llm=True,
            fallback_to_rules=False,
        )
        return _record_intake_memory(
            user_id,
            intake,
            source=source,
            confirmed=confirmed,
        )
    except Exception:
        return []


def merge_into_prompt(
    base_input: str,
    user_id: str,
    *,
    confidence_threshold: float = 0.4,
    max_lines: int = 8,
) -> str:
    """Inject only confirmed, active memory into the planner prompt."""
    if not user_id:
        return base_input
    prefs = get_preferences(
        user_id,
        apply_decay=True,
        confirmed_only=True,
        include_expired=False,
    )
    relevant = [entry for entry in prefs if entry.confidence >= confidence_threshold]
    if not relevant:
        return base_input
    relevant.sort(key=lambda entry: (entry.confidence, entry.mention_count), reverse=True)

    lines = ["", "[用户长期偏好（由用户确认，可随时删除）]"]
    for entry in relevant[:max_lines]:
        marker = "✓" if entry.kind == "preference" else "✗" if entry.kind == "dislike" else "·"
        display_value = _prompt_value(entry.mem_value)
        memory_fact = (
            entry.mem_key
            if not display_value
            else f"{entry.mem_key} = {display_value}"
        )
        lines.append(
            f"- {marker} {memory_fact} "
            f"(revision {entry.revision}, 提及 {entry.mention_count}次, 置信 {entry.confidence:.2f})"
        )
    return base_input + "\n".join(lines) if base_input else "\n".join(lines[1:])


def _canonical_value(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("memory value must be JSON serializable") from exc


def _prompt_value(value: object) -> str:
    if value is True or value is None:
        return ""
    if value is False:
        return "false"
    if isinstance(value, (str, int, float)):
        rendered = str(value)
    else:
        rendered = _canonical_value(value)
    return rendered[:80]


def _value_sha256(value_json: str) -> str:
    return hashlib.sha256(value_json.encode("utf-8")).hexdigest()


def _append_event(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    kind: str,
    key: str,
    event_type: str,
    revision: int,
    source: str,
    value_sha256: Optional[str],
    previous_value_sha256: Optional[str],
    reason: str,
    created_at: float,
) -> None:
    conn.execute(
        """
        INSERT INTO user_memory_events(
            user_id, kind, mem_key, event_type, revision, source,
            value_sha256, previous_value_sha256, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            kind,
            key,
            event_type,
            revision,
            source,
            value_sha256,
            previous_value_sha256,
            reason,
            created_at,
        ),
    )


def _entry_from_row(row: sqlite3.Row, *, now: float, apply_decay: bool) -> MemoryEntry:
    confidence = float(row["confidence"])
    if apply_decay and (now - float(row["last_seen_at"])) / 86400 > DECAY_DAYS:
        confidence = round(confidence * DECAY_FACTOR, 3)
    expires_at = float(row["expires_at"]) if row["expires_at"] is not None else None
    return MemoryEntry(
        user_id=row["user_id"],
        kind=row["kind"],
        mem_key=row["mem_key"],
        mem_value=json.loads(row["mem_value"]),
        confidence=confidence,
        mention_count=int(row["mention_count"]),
        first_seen_at=float(row["first_seen_at"]),
        last_seen_at=float(row["last_seen_at"]),
        source=row["source"],
        confirmed_at=(float(row["confirmed_at"]) if row["confirmed_at"] is not None else None),
        expires_at=expires_at,
        revision=int(row["revision"] or 1),
        forgotten=bool(row["forgotten"]),
        expired=expires_at is not None and expires_at <= now,
    )


def _event_from_row(row: sqlite3.Row) -> MemoryEvent:
    return MemoryEvent(
        event_id=int(row["event_id"]),
        user_id=row["user_id"],
        kind=row["kind"],
        mem_key=row["mem_key"],
        event_type=row["event_type"],
        revision=int(row["revision"]),
        source=row["source"],
        value_sha256=row["value_sha256"],
        previous_value_sha256=row["previous_value_sha256"],
        reason=row["reason"],
        created_at=float(row["created_at"]),
    )
