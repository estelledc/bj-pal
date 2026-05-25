"""LLM 调用鲁棒性工具层。

解决 40 场景跑测发现的两类失败：
- 8/40 RPM 限流 → 客户端令牌桶 + 指数退避（不撞墙）
- 4/40 JSON 截断 → 多策略恢复（栈补全 / steps 数组单独恢复）

不侵入业务代码，由 LongCatClient.complete 在边界调一层即可。
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from collections import deque
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)


# ============================================================
# RateLimiter — 滑动窗口令牌桶
# ============================================================

class RateLimiter:
    """RPM 限速器（滑动 60s 窗口）。

    用法：
        limiter = RateLimiter(rpm=10)
        with limiter:
            client.messages.create(...)
    """

    def __init__(self, rpm: int = 10):
        self.rpm = rpm
        self.window = 60.0
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > self.window:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.rpm:
                wait = self.window - (now - self._timestamps[0]) + 0.2
                logger.info(f"[RateLimiter] hit RPM={self.rpm}, sleep {wait:.1f}s")
                time.sleep(wait)
                # 释放最早一个槽（窗口已滑动）
                if self._timestamps:
                    self._timestamps.popleft()
            self._timestamps.append(time.monotonic())

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        return False


_global_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_global_limiter(rpm: Optional[int] = None) -> RateLimiter:
    """全局单例 RPM 限速器。所有 LongCat 调用共用一个。"""
    global _global_limiter
    with _limiter_lock:
        if _global_limiter is None:
            import os
            default_rpm = int(os.environ.get("BJ_PAL_LLM_RPM", "10"))
            _global_limiter = RateLimiter(rpm=rpm or default_rpm)
        return _global_limiter


# ============================================================
# Retry — 指数退避 + jitter
# ============================================================

T = TypeVar("T")


def is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    if any(k in msg for k in ("rpm", "429", "rate limit", "rate_limit", "限制", "超过")):
        return True
    if e.__class__.__name__ in ("RateLimitError",):
        return True
    return False


def is_transient_error(e: Exception) -> bool:
    msg = str(e).lower()
    if any(k in msg for k in ("timeout", "connection", "502", "503", "504", "reset", "broken pipe")):
        return True
    if e.__class__.__name__ in ("APIConnectionError", "APITimeoutError", "ReadTimeout", "InternalServerError"):
        return True
    return False


def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: float = 0.5,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> T:
    """指数退避调用。

    delay_n = min(max_delay, base_delay * 2^(n-1)) * (1 ± jitter)

    只对限流 / 瞬时网络错误重试；其他直接抛。
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if not (is_rate_limit_error(e) or is_transient_error(e)):
                raise
            if attempt >= max_attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = max(0.5, delay * (1 + random.uniform(-jitter, jitter)))
            logger.warning(
                f"[retry] attempt {attempt}/{max_attempts} failed "
                f"({e.__class__.__name__}: {str(e)[:120]}), sleep {delay:.1f}s"
            )
            if on_retry:
                on_retry(attempt, e, delay)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ============================================================
# JSON 鲁棒解析（含截断恢复）
# ============================================================

def repair_json(text: str) -> Optional[dict]:
    """LLM JSON 输出鲁棒解析。

    顺序：
    1. 直接 json.loads
    2. 去 ```json``` 包裹再 parse
    3. 提取最外层 {...} parse
    4. 截断恢复：栈补全 `}` `]`
    5. 兜底：从 'steps': [...] 中抠出能完整解析的 step 子集
    """
    if not text:
        return None
    s = text.strip()

    # 1) 直接 parse
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 2) 去 markdown 代码块
    s2 = _strip_code_fence(s)
    if s2 != s:
        try:
            return json.loads(s2)
        except json.JSONDecodeError:
            pass

    # 3) 抓最外层 {...}
    start = s2.find("{")
    if start == -1:
        return None
    end = s2.rfind("}")
    if end > start:
        try:
            return json.loads(s2[start:end + 1])
        except json.JSONDecodeError:
            pass

    # 4) 截断恢复
    truncated = s2[start:]
    repaired = _close_truncated_json(truncated)
    if repaired is not None:
        try:
            d = json.loads(repaired)
            d["_repaired"] = "stack_close"
            return d
        except json.JSONDecodeError:
            pass

    # 5) 兜底恢复 steps
    return _recover_steps_only(s2)


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _close_truncated_json(s: str) -> Optional[str]:
    """对截断的 JSON 字符串补全闭合。

    扫描所有未配对的 `{` `[` `"`，根据栈状态补齐 `}` `]`。
    遇到字符串中间被截断时，先回退到最近的安全切点（逗号 / 已闭合大括号）。
    """
    if not s or not s.startswith("{"):
        return None

    stack: list[str] = []
    in_str = False
    escape = False
    last_safe_cut = -1  # 最近的字符串外的 `,` 或 `}` `]`

    for i, c in enumerate(s):
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            if not in_str:
                last_safe_cut = i
            continue
        if in_str:
            continue
        if c in "{[":
            stack.append(c)
        elif c in "}]":
            if stack:
                stack.pop()
            last_safe_cut = i
        elif c == ",":
            last_safe_cut = i

    # 字符串中间截断 → 切到最近安全点
    if in_str:
        if last_safe_cut == -1:
            return None
        s = s[:last_safe_cut + 1]
        # 重建 stack
        stack = []
        in_str = False
        escape = False
        for c in s:
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c in "{[":
                stack.append(c)
            elif c in "}]":
                if stack:
                    stack.pop()

    s = s.rstrip()
    # 去尾逗号；若刚刚是 `key:` 没 value 也截掉
    while True:
        if s.endswith(","):
            s = s[:-1].rstrip()
            continue
        # `"key":` 但没接 value → 截掉这段 key
        m = re.search(r',?\s*"[^"]*"\s*:\s*$', s)
        if m:
            s = s[:m.start()].rstrip()
            continue
        break

    closing = "".join("}" if op == "{" else "]" for op in reversed(stack))
    return s + closing


def _recover_steps_only(s: str) -> Optional[dict]:
    """从文本里抠出 `"steps": [...]` 数组里能完整解析的对象。

    用于 stack 闭合也失败的极端截断（如 step 中间字段名都断了）。
    """
    m = re.search(r'"steps"\s*:\s*\[', s)
    if not m:
        return None
    start = m.end()

    steps: list = []
    depth = 0
    cur_start = -1
    in_str = False
    escape = False

    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            if depth == 0:
                cur_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and cur_start != -1:
                try:
                    steps.append(json.loads(s[cur_start:i + 1]))
                except json.JSONDecodeError:
                    pass
                cur_start = -1
        elif c == "]" and depth == 0:
            break

    if not steps:
        return None
    return {"steps": steps, "_repaired": "steps_only"}


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cases = {
        "完整": '{"steps":[{"step_index":1}]}',
        "markdown 包裹": '```json\n{"a":1}\n```',
        "前缀解释": 'Here is the plan:\n{"a": 1, "b": [1,2]}',
        "数字截断": '{"steps":[{"step_index":1},{"step_index":2',
        "字符串截断": '{"steps":[{"step_index":1},{"step_index":2,"name":"故宫博',
        "key 后无 value": '{"steps":[{"step_index":1}],"persona":"family","extra":',
        "完全垃圾": "I don't think I should output JSON.",
    }
    for label, raw in cases.items():
        r = repair_json(raw)
        ok = "✓" if r else "✗"
        print(f"{ok} {label:14s}: {str(r)[:100]}")
