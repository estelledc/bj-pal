from __future__ import annotations

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import user_memory  # noqa: E402


@pytest.fixture
def isolated_memory_db(tmp_path, monkeypatch):
    database = tmp_path / "memory.db"
    monkeypatch.setattr(user_memory, "_DB_PATH", database)
    user_memory._ensure_schema()
    return database


def test_existing_v2_schema_is_migrated_without_losing_state(tmp_path, monkeypatch) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                mem_key TEXT NOT NULL,
                mem_value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.7,
                mention_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                forgotten INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, kind, mem_key)
            );
            CREATE INDEX idx_user_memory_user ON user_memory(user_id, forgotten);
            """
        )
        connection.execute(
            "INSERT INTO user_memory(user_id, kind, mem_key, mem_value, confidence, "
            "mention_count, first_seen_at, last_seen_at, forgotten) "
            "VALUES ('legacy-user', 'fact', 'area:city', '\"北京\"', 0.8, 2, 1, 2, 0)"
        )

    monkeypatch.setattr(user_memory, "_DB_PATH", database)
    user_memory._ensure_schema()

    entries = user_memory.get_preferences("legacy-user", apply_decay=False)
    assert len(entries) == 1
    assert entries[0].mem_value == "北京"
    assert entries[0].source == "legacy"
    assert entries[0].revision == 1
    assert entries[0].confirmed is False
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(user_memory)")}
    assert {"source", "confirmed_at", "expires_at", "revision"} <= columns


def test_same_value_reinforces_but_explicit_change_starts_new_revision(
    isolated_memory_db,
) -> None:
    del isolated_memory_db
    created = user_memory.upsert_memory(
        "user-a",
        "area:current_city",
        "北京",
        kind="fact",
        source="explicit_user_input",
        confirmed=True,
    )
    reinforced = user_memory.upsert_memory(
        "user-a",
        "area:current_city",
        "北京",
        kind="fact",
        confidence=0.9,
        source="explicit_user_input",
        confirmed=True,
    )
    replaced = user_memory.upsert_memory(
        "user-a",
        "area:current_city",
        "上海",
        kind="fact",
        source="explicit_user_input",
        confirmed=True,
    )

    assert created.action == "created"
    assert reinforced.action == "reinforced"
    assert reinforced.entry.mention_count == 2
    assert replaced.action == "replaced"
    assert replaced.entry.mem_value == "上海"
    assert replaced.entry.mention_count == 1
    assert replaced.entry.revision == 2
    assert replaced.previous_value_sha256 != replaced.incoming_value_sha256
    assert [event.event_type for event in user_memory.list_memory_events("user-a")] == [
        "created",
        "reinforced",
        "replaced",
    ]


def test_unconfirmed_conflict_cannot_overwrite_confirmed_memory(isolated_memory_db) -> None:
    del isolated_memory_db
    user_memory.upsert_memory(
        "user-b",
        "area:current_city",
        "上海",
        kind="fact",
        source="manual_entry",
        confirmed=True,
    )
    rejected = user_memory.upsert_memory(
        "user-b",
        "area:current_city",
        "广州",
        kind="fact",
        source="inferred",
        confirmed=False,
    )

    assert rejected.action == "conflict_rejected"
    assert rejected.entry.mem_value == "上海"
    assert rejected.entry.revision == 1
    event = user_memory.list_memory_events("user-b")[-1]
    assert event.event_type == "conflict_rejected"
    assert event.reason == "unconfirmed_value_conflicts_with_active_memory"
    assert "上海" not in str(event)
    assert "广州" not in str(event)


def test_only_confirmed_non_expired_memory_enters_prompt(isolated_memory_db) -> None:
    del isolated_memory_db
    user_memory.upsert_memory(
        "user-c",
        "area:current_city",
        "北京",
        kind="fact",
        source="inferred",
        confirmed=False,
    )
    assert user_memory.merge_into_prompt("周末去哪", "user-c") == "周末去哪"

    assert user_memory.confirm_memory("user-c", "area:current_city", kind="fact")
    prompt = user_memory.merge_into_prompt("周末去哪", "user-c")
    assert "area:current_city = 北京" in prompt

    user_memory.upsert_memory(
        "user-c",
        "area:temporary_city",
        "天津",
        kind="fact",
        source="manual_entry",
        confirmed=True,
        expires_at=time.time() - 1,
    )
    assert "天津" not in user_memory.merge_into_prompt("周末去哪", "user-c")
    expired = user_memory.get_preferences("user-c", include_expired=True)
    assert any(entry.mem_key == "area:temporary_city" and entry.expired for entry in expired)


def test_expired_memory_can_be_replaced_without_implicit_activation(isolated_memory_db) -> None:
    del isolated_memory_db
    user_memory.upsert_memory(
        "user-d",
        "area:trip_city",
        "北京",
        kind="fact",
        source="manual_entry",
        confirmed=True,
        expires_at=time.time() - 1,
    )
    replacement = user_memory.upsert_memory(
        "user-d",
        "area:trip_city",
        "上海",
        kind="fact",
        source="inferred",
        confirmed=False,
        expires_at=time.time() + 3600,
    )
    assert replacement.action == "replaced"
    assert replacement.entry.mem_value == "上海"
    assert replacement.entry.confirmed is False
    assert user_memory.merge_into_prompt("query", "user-d") == "query"


def test_soft_forget_is_reversible_but_hard_delete_purges_state_and_events(
    isolated_memory_db,
) -> None:
    del isolated_memory_db
    user_memory.record_preference("user-e", "taste:coffee", True)
    assert user_memory.forget("user-e", "taste:coffee")
    assert user_memory.get_preferences("user-e") == []
    assert len(user_memory.get_preferences("user-e", include_forgotten=True)) == 1
    assert user_memory.list_memory_events("user-e")

    assert user_memory.delete_memory("user-e", "taste:coffee")
    assert user_memory.get_preferences("user-e", include_forgotten=True) == []
    assert user_memory.list_memory_events("user-e") == ()


def test_memory_events_are_immutable_but_support_cursor_replay(isolated_memory_db) -> None:
    database = isolated_memory_db
    user_memory.record_preference("user-f", "taste:coffee", True)
    user_memory.record_preference("user-f", "taste:coffee", True)
    events = user_memory.list_memory_events("user-f", limit=10)
    assert len(events) == 2
    replay = user_memory.list_memory_events(
        "user-f",
        after_event_id=events[0].event_id,
        limit=1,
    )
    assert [event.event_type for event in replay] == ["reinforced"]

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE user_memory_events SET reason='tampered' WHERE event_id=?",
                (events[0].event_id,),
            )


def test_delete_all_is_a_real_privacy_purge(isolated_memory_db) -> None:
    del isolated_memory_db
    user_memory.record_preference("user-g", "taste:coffee", True)
    user_memory.record_preference("user-g", "diet:no_spicy", True, kind="dislike")
    assert user_memory.delete_all("user-g") == 2
    assert user_memory.get_preferences("user-g", include_forgotten=True) == []
    assert user_memory.list_memory_events("user-g") == ()


def test_concurrent_same_value_writes_do_not_lose_reinforcements(isolated_memory_db) -> None:
    del isolated_memory_db

    def write_once(_index: int) -> None:
        user_memory.record_preference("user-h", "taste:coffee", True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_once, range(12)))

    entry = user_memory.get_preferences("user-h", apply_decay=False)[0]
    assert entry.mention_count == 12
    assert entry.revision == 1
    events = user_memory.list_memory_events("user-h", limit=20)
    assert len(events) == 12
    assert events[0].event_type == "created"
    assert all(event.event_type == "reinforced" for event in events[1:])
