"""Request-local tracing with privacy-minimized JSONL/OTLP export.

用途：在 hackathon demo 里把 planner / replanner / llm_client / 工具调用
绑成一棵 trace 树，便于复现 + 性能分析 + 失败复盘。

后端选择（环境变量 BJ_PAL_TRACE）：
- off    （默认）   不写 trace，零开销
- jsonl              写 data/traces/trace-{session_hash}.jsonl，每行一个脱敏 span
- otlp               用 OTLP/HTTP protobuf 批量导出；必须显式配置 endpoint
- otel               `otlp` 的兼容别名，不再向 stdout 打印或静默回退 JSONL

API：
- with trace_span("name", attrs={...}) as sp:
       sp.set_attribute("k", "v")
       sp.set_status("ok" | "error", error_msg)

- @traced("name") 函数装饰器

设计：
- span 之间通过 ContextVar 维护 parent；async task 继承 context，新线程须显式复制 context
- session_id 由 set_session() 设置，但 exporter 只保留 SHA，不输出原值
- 后端 = off 时，普通调用仍是 no-op；PlanningService 可显式开启内存 capture
  生成可验证的 execution observation，不依赖外部 collector
- export I/O 失败不污染业务结果，但会进入 payload-free health snapshot
"""
from __future__ import annotations

import contextvars
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .trace_export import (
    MonitoredSpanExporter,
    TraceExportMonitor,
    TraceExportStatus,
    apply_span_projection,
    hashed_trace_filename,
    safe_span_name,
    sanitized_jsonl_payload,
    validate_otlp_http_configuration,
)

# ============================================================
# 后端检测 + 单例
# ============================================================

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
    backend_handle: Any = field(default=None, repr=False, compare=False)

_current_span: contextvars.ContextVar[Optional[_SpanRecord]] = contextvars.ContextVar(
    "bj_pal_current_span", default=None
)
_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bj_pal_trace_session", default=""
)
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bj_pal_trace_id", default=""
)
_execution_capture: contextvars.ContextVar[Optional["ExecutionTraceCapture"]] = (
    contextvars.ContextVar("bj_pal_execution_capture", default=None)
)


# ============================================================
# Backend 抽象
# ============================================================

class _Backend:
    def begin(self, sp: _SpanRecord, parent_handle: Any = None) -> Any:
        return None

    def emit(self, sp: _SpanRecord) -> None:
        ...

    def force_flush(self, timeout_millis: int = 5000) -> bool:
        return True

    def shutdown(self) -> None:
        return


class _NoopBackend(_Backend):
    def emit(self, sp: _SpanRecord) -> None:
        return


class _JsonlBackend(_Backend):
    def __init__(self, dir_path: Path, monitor: TraceExportMonitor):
        self.dir = dir_path
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._monitor = monitor
        monitor.configure(backend="jsonl", state="configured_unproven", processor="sync")

    def emit(self, sp: _SpanRecord) -> None:
        path = self.dir / hashed_trace_filename(sp.session_id)
        payload, dropped = sanitized_jsonl_payload(sp)
        import json

        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            with self._lock:
                with path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError:
            self._monitor.record_batch_result(
                span_count=1,
                succeeded=False,
                error_code="jsonl_write_failed",
            )
            raise
        self._monitor.record_projection(dropped)
        self._monitor.record_local_export()


class _OtlpBackend(_Backend):
    """OpenTelemetry SDK adapter with batch OTLP/HTTP export."""

    def __init__(self, monitor: TraceExportMonitor):
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint_hash = validate_otlp_http_configuration(dict(os.environ))
        resource = Resource.create(
            {
                "service.name": "bj-pal",
                "service.version": "6.21.0",
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = MonitoredSpanExporter(OTLPSpanExporter(), monitor)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        self._provider = provider
        self._tracer = provider.get_tracer("bj-pal", "6.21.0")
        self._otel_trace = otel_trace
        self._monitor = monitor
        monitor.configure(
            backend="otlp",
            state="configured_unproven",
            processor="batch",
            endpoint_origin_sha256=endpoint_hash,
        )

    def begin(self, sp: _SpanRecord, parent_handle: Any = None) -> Any:
        context = None
        if parent_handle is not None:
            context = self._otel_trace.set_span_in_context(parent_handle)
        return self._tracer.start_span(
            safe_span_name(sp.name),
            context=context,
            start_time=int(sp.start_ts * 1e9),
        )

    def emit(self, sp: _SpanRecord) -> None:
        from opentelemetry.trace import StatusCode, Status
        otel_span = sp.backend_handle
        if otel_span is None:
            return
        dropped = apply_span_projection(otel_span, sp)
        self._monitor.record_projection(dropped)
        if sp.status == "error":
            otel_span.set_status(Status(StatusCode.ERROR))
        else:
            otel_span.set_status(Status(StatusCode.OK))
        otel_span.end(end_time=int(sp.end_ts * 1e9))

    def force_flush(self, timeout_millis: int = 5000) -> bool:
        try:
            return self._provider.force_flush(timeout_millis)
        except Exception:
            self._monitor.record_runtime_failure("force_flush_failed")
            return False

    def shutdown(self) -> None:
        self._provider.shutdown()


class _DegradedBackend(_NoopBackend):
    """Non-fatal sink used when explicit export configuration is invalid."""


_backend_instance: Optional[_Backend] = None
_backend_lock = threading.Lock()
_export_monitor = TraceExportMonitor()


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
            _export_monitor.configure(
                backend="off",
                state="disabled",
                processor="none",
            )
        elif choice in {"otel", "otlp"}:
            try:
                _backend_instance = _OtlpBackend(_export_monitor)
            except Exception as exc:
                error_code = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "exporter_initialization_failed"
                )
                _export_monitor.configure(
                    backend="otlp",
                    state="degraded",
                    processor="none",
                    error_code=error_code,
                )
                _backend_instance = _DegradedBackend()
        elif choice == "jsonl":
            try:
                _backend_instance = _JsonlBackend(
                    _default_jsonl_dir(),
                    _export_monitor,
                )
            except Exception:
                _export_monitor.configure(
                    backend="jsonl",
                    state="degraded",
                    processor="none",
                    error_code="jsonl_initialization_failed",
                )
                _backend_instance = _DegradedBackend()
        else:
            _export_monitor.configure(
                backend="invalid",
                state="degraded",
                processor="none",
                error_code="invalid_backend",
            )
            _backend_instance = _DegradedBackend()
    return _backend_instance


def _default_jsonl_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "traces"


# ============================================================
# 公共 API
# ============================================================

class ExecutionTraceCapture:
    """Request-local in-memory span sink used by the application boundary.

    The snapshot deliberately excludes span attributes except provider-reported
    numeric token usage. User input, prompts, POI decisions, and user IDs must
    not enter the public execution artifact.
    """

    def __init__(self, *, correlation_id: str | None = None) -> None:
        self.execution_id = f"exec-{uuid.uuid4().hex}"
        self.correlation_id = correlation_id
        self.trace_id = uuid.uuid4().hex
        self._records: list[_SpanRecord] = []
        self._lock = threading.Lock()

    def record(self, span: _SpanRecord) -> None:
        with self._lock:
            self._records.append(span)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            ordered = sorted(
                self._records,
                key=lambda item: (item.start_ts, item.end_ts, item.span_id),
            )
        return {
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "spans": [
                {
                    "name": item.name,
                    "span_id": item.span_id,
                    "parent_span_id": item.parent_id,
                    "started_at_epoch": item.start_ts,
                    "duration_ms": round(max(0.0, item.end_ts - item.start_ts) * 1000, 3),
                    "status": item.status,
                    "input_tokens": _reported_token(item.attrs.get("input_tokens")),
                    "output_tokens": _reported_token(item.attrs.get("output_tokens")),
                }
                for item in ordered
            ],
        }


def _reported_token(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@contextmanager
def capture_execution(correlation_id: str | None = None):
    """Capture one execution without requiring JSONL or an OTEL collector."""
    capture = ExecutionTraceCapture(correlation_id=correlation_id)
    capture_token = _execution_capture.set(capture)
    session_token = _session_id.set(correlation_id or capture.execution_id)
    trace_token = _trace_id.set(capture.trace_id)
    try:
        yield capture
    finally:
        _trace_id.reset(trace_token)
        _session_id.reset(session_token)
        _execution_capture.reset(capture_token)

def set_session(session_id: str) -> None:
    """绑定当前 ContextVar 的 session_id；同时新开 trace_id。"""
    _session_id.set(session_id)
    _trace_id.set(uuid.uuid4().hex)


def get_session() -> str:
    return _session_id.get() or ""


def reset_backend_for_tests() -> None:
    """测试用：强制 backend 单例重新加载（让 BJ_PAL_TRACE env 改动生效）。"""
    global _backend_instance
    if _backend_instance is not None:
        try:
            _backend_instance.shutdown()
        except Exception:
            pass
    _backend_instance = None
    _export_monitor.reset()


def trace_export_status(*, resolve: bool = True) -> TraceExportStatus:
    """Return payload-free export health; optionally initialize configured backend."""
    if resolve:
        _resolve_backend()
    return _export_monitor.snapshot()


def force_flush_trace_export(timeout_millis: int = 5000) -> bool:
    """Flush queued spans without exposing collector credentials or payloads."""
    if timeout_millis < 1 or timeout_millis > 30_000:
        raise ValueError("trace export flush timeout must be between 1 and 30000 ms")
    return _resolve_backend().force_flush(timeout_millis)


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
    # The request budget is independent from the export backend. Enforce it
    # before the bounded operation begins, including when tracing is `off`.
    from .execution_budget import current_execution_budget

    execution_budget = current_execution_budget()
    if execution_budget is not None:
        execution_budget.before_span(name)
    capture = _execution_capture.get()
    backend_enabled = (os.environ.get("BJ_PAL_TRACE") or "off").lower() != "off"
    if not backend_enabled and capture is None:
        # 完全 no-op，zero overhead
        record = _SpanRecord(
            name=name, span_id="", parent_id=None, trace_id="",
            session_id="", start_ts=time.time(), end_ts=time.time(),
            attrs=attrs or {},
        )
        span = _Span(record)
        try:
            yield span
            record.status = "ok"
        except Exception as exc:
            record.status = "error"
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record.end_ts = time.time()
            if execution_budget is not None and record.status == "ok":
                execution_budget.after_span(record.name, record.attrs)
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
    backend = _resolve_backend() if backend_enabled else None
    if backend is not None:
        try:
            rec.backend_handle = backend.begin(
                rec,
                parent.backend_handle if parent is not None else None,
            )
        except Exception:
            rec.backend_handle = None
            _export_monitor.record_runtime_failure("span_start_failed")
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
        if capture is not None:
            capture.record(rec)
        try:
            if backend is not None:
                backend.emit(rec)
        except Exception:
            # trace 不能让业务挂
            _export_monitor.record_runtime_failure("span_emit_failed")
        # Provider-reported usage only exists after the operation returns.
        # Enforcing here stops the next stage without pretending the tokens
        # already spent by that call can be recovered.
        if execution_budget is not None and rec.status == "ok":
            execution_budget.after_span(rec.name, rec.attrs)


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
