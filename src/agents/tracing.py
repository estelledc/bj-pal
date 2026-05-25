"""[88] OpenTelemetry tracing — 可选依赖，未装走 JSONL fallback。

用途：在 hackathon demo 里把 planner / replanner / llm_client / 工具调用
绑成一棵 trace 树，便于复现 + 性能分析 + 失败复盘。

后端选择（环境变量 BJ_PAL_TRACE）：
- off    （默认）   不写 trace，零开销
- jsonl              写 data/traces/{session_id}.jsonl，每行一个 span
- otel               用 opentelemetry SDK + ConsoleSpanExporter
                     （安装 opentelemetry-sdk 后启用，未装自动降级到 jsonl）

API：
- with trace_span("name", attrs={...}) as sp:
       sp.set_attribute("k", "v")
       sp.set_status("ok" | "error", error_msg)

- @traced("name") 函数装饰器

设计：
- span 之间通过 ContextVar 维护 parent，跨线程 / asyncio 自动衔接
- session_id 由 set_session() 设置，写到每个 span 的 attrs
- 即使后端 = off，with 块和装饰器仍正常运行（no-op span）
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ============================================================
# 后端检测 + 单例
# ============================================================

_BACKEND_ENV = os.environ.get("BJ_PAL_TRACE", "off").lower()


@dataclass
class _SpanRecord:
    name: str
    span_id: str
    parent_id: Optional[str]
    trace_id: str
    session_id: str
    start_ts: float
    end_ts: float = 0.0
    attrs: dict = field(default_factory=dict)
    status: str = "unset"
    error: str = ""

    def to_jsonl(self) -> str:
        return json.dumps({
            "name": self.name,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "start_ts": round(self.start_ts, 6),
            "duration_ms": round((self.end_ts - self.start_ts) * 1000, 2),
            "attrs": self.attrs,
            "status": self.status,
            "error": self.error or None,
        }, ensure_ascii=False)


_current_span: contextvars.ContextVar[Optional[_SpanRecord]] = contextvars.ContextVar(
    "bj_pal_current_span", default=None
)
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bj_pal_trace_session", default=""
)
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bj_pal_trace_id", default=""
)


# ============================================================
# Backend 抽象
# ============================================================

class _Backend:
    def emit(self, sp: _SpanRecord) -> None:
        ...


class _NoopBackend(_Backend):
    def emit(self, sp: _SpanRecord) -> None:
        return


class _JsonlBackend(_Backend):
    def __init__(self, dir_path: Path):
        self.dir = dir_path
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, sp: _SpanRecord) -> None:
        sid = sp.session_id or "default"
        path = self.dir / f"{sid}.jsonl"
        line = sp.to_jsonl()
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class _OtelBackend(_Backend):
    """OpenTelemetry SDK 包装。"""

    def __init__(self):
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
        resource = Resource.create({"service.name": "bj-pal"})
        provider = TracerProvider(resource=resource)
        exporter = ConsoleSpanExporter()
        # Simple（非 Batch）保证 hackathon demo 能立即看到 trace
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        otel_trace.set_tracer_provider(provider)
        self._tracer = otel_trace.get_tracer("bj-pal")
        # 维护一个 span_id → otel span 映射，因为我们用自己的 _SpanRecord 树
        self._otel_spans: dict[str, Any] = {}
        self._lock = threading.Lock()

    def emit(self, sp: _SpanRecord) -> None:
        # 已 emit 的 span 都已 finish，OTel 立即看见
        # 这里做 "事后 emit"：在 _SpanRecord end 时一次性创建 OTel span
        # 用 start_as_current_span 兼容上下文
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import StatusCode, Status
        # 用 OTel 的 manual span（不进入 context）
        otel_span = self._tracer.start_span(
            sp.name,
            start_time=int(sp.start_ts * 1e9),
        )
        for k, v in sp.attrs.items():
            try:
                otel_span.set_attribute(k, v)
            except Exception:
                otel_span.set_attribute(k, str(v))
        otel_span.set_attribute("session_id", sp.session_id)
        if sp.status == "error":
            otel_span.set_status(Status(StatusCode.ERROR, sp.error or "error"))
        else:
            otel_span.set_status(Status(StatusCode.OK))
        otel_span.end(end_time=int(sp.end_ts * 1e9))


_backend_instance: Optional[_Backend] = None
_backend_lock = threading.Lock()


def _resolve_backend() -> _Backend:
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance
    with _backend_lock:
        if _backend_instance is not None:
            return _backend_instance
        choice = (os.environ.get("BJ_PAL_TRACE") or "off").lower()
        if choice == "off":
            _backend_instance = _NoopBackend()
        elif choice == "otel":
            try:
                _backend_instance = _OtelBackend()
            except Exception:
                # OTel SDK 未装 → 降级到 JSONL
                _backend_instance = _JsonlBackend(_default_jsonl_dir())
        else:
            # 默认或显式 "jsonl"
            _backend_instance = _JsonlBackend(_default_jsonl_dir())
    return _backend_instance


def _default_jsonl_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "traces"


# ============================================================
# 公共 API
# ============================================================

def set_session(session_id: str) -> None:
    """绑定当前 ContextVar 的 session_id；同时新开 trace_id。"""
    _session_id.set(session_id)
    _trace_id.set(uuid.uuid4().hex)


def get_session() -> str:
    return _session_id.get() or ""


def reset_backend_for_tests() -> None:
    """测试用：强制 backend 单例重新加载（让 BJ_PAL_TRACE env 改动生效）。"""
    global _backend_instance
    _backend_instance = None


class _Span:
    """trace_span() 上下文管理器内部对象。"""

    def __init__(self, record: _SpanRecord):
        self._r = record

    def set_attribute(self, key: str, value: Any) -> None:
        self._r.attrs[key] = value

    def set_status(self, status: str, error: str = "") -> None:
        self._r.status = status
        if error:
            self._r.error = error

    @property
    def span_id(self) -> str:
        return self._r.span_id

    @property
    def trace_id(self) -> str:
        return self._r.trace_id


@contextmanager
def trace_span(name: str, attrs: Optional[dict] = None):
    """开 span。"""
    if (os.environ.get("BJ_PAL_TRACE") or "off").lower() == "off":
        # 完全 no-op，zero overhead
        yield _Span(_SpanRecord(
            name=name, span_id="", parent_id=None, trace_id="",
            session_id="", start_ts=time.time(), end_ts=time.time(),
            attrs=attrs or {},
        ))
        return

    parent = _current_span.get()
    sid = _session_id.get()
    tid = _trace_id.get() or uuid.uuid4().hex
    if not _trace_id.get():
        _trace_id.set(tid)

    rec = _SpanRecord(
        name=name,
        span_id=uuid.uuid4().hex[:16],
        parent_id=parent.span_id if parent else None,
        trace_id=tid,
        session_id=sid,
        start_ts=time.time(),
        attrs=dict(attrs or {}),
    )
    span = _Span(rec)
    token = _current_span.set(rec)
    try:
        yield span
        if rec.status == "unset":
            rec.status = "ok"
    except Exception as exc:
        rec.status = "error"
        rec.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        rec.end_ts = time.time()
        _current_span.reset(token)
        try:
            _resolve_backend().emit(rec)
        except Exception:
            # trace 不能让业务挂
            pass


def traced(name: Optional[str] = None, **default_attrs):
    """函数装饰器版本，自动用函数名当 span name。"""
    def deco(fn):
        span_name = name or f"{fn.__module__}.{fn.__name__}"

        def wrapper(*args, **kwargs):
            with trace_span(span_name, attrs=dict(default_attrs)):
                return fn(*args, **kwargs)

        wrapper.__wrapped__ = fn
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return deco
