from __future__ import annotations

import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tools import tool_call_log  # noqa: E402


def test_concurrent_tool_logs_keep_request_local_session_ids(tmp_path, monkeypatch) -> None:
    database = tmp_path / "tool-calls.db"
    monkeypatch.setattr(tool_call_log, "LOG_DB", database)
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors: list[Exception] = []

    def write(index: int) -> None:
        try:
            session_id = f"session-{index}"
            tool_call_log.set_session(session_id)
            barrier.wait(timeout=5)
            tool_call_log.log_call("fixture", params={"index": index})
        except Exception as exc:  # surface worker exceptions in the test thread
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    rows = tool_call_log.fetch_calls(limit=thread_count * 2)
    assert {row["session_id"] for row in rows} == {
        f"session-{index}" for index in range(thread_count)
    }
