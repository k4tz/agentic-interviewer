from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic_ns
from typing import Protocol
from uuid import uuid4

_SAFE_ATTRIBUTE = re.compile(r"^[a-zA-Z0-9_.:/-]{0,128}$")
_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_SENSITIVE_ATTRIBUTE_PARTS = frozenset(
    {"answer", "audio", "candidate", "content", "email", "prompt", "resume", "text", "transcript"}
)


def tag_hash(value: str, *, salt: str) -> str:
    """Produce a stable correlation tag without exposing the source identifier."""
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Correlation:
    interview_id: str
    turn_id: str
    tenant_tag: str
    graph_thread_id: str | None = None
    deployment_version: str = "development"

    @classmethod
    def from_ids(cls, *, tenant_id: str, interview_id: str, turn_id: str, salt: str) -> Correlation:
        return cls(interview_id, turn_id, tag_hash(tenant_id, salt=salt))

    def attributes(self) -> dict[str, str]:
        values = {
            "interview.id": self.interview_id,
            "turn.id": self.turn_id,
            "tenant.tag": self.tenant_tag,
            "graph.thread_id": self.graph_thread_id,
            "deployment.version": self.deployment_version,
        }
        return {key: value for key, value in values.items() if value is not None}


def safe_attributes(attributes: Mapping[str, object]) -> dict[str, str | int | float | bool]:
    safe: dict[str, str | int | float | bool] = {}
    for key, value in attributes.items():
        if not _SAFE_ATTRIBUTE.fullmatch(key):
            continue
        if _SENSITIVE_ATTRIBUTE_PARTS.intersection(re.split(r"[._:/-]", key.lower())):
            continue
        if isinstance(value, bool | int | float):
            safe[key] = value
        elif isinstance(value, str) and _SAFE_ATTRIBUTE.fullmatch(value):
            safe[key] = value
    return safe


@dataclass(frozen=True)
class SpanRecord:
    trace_id: str
    span_id: str
    name: str
    duration_ms: float
    status: str
    attributes: dict[str, str | int | float | bool] = field(default_factory=dict)


class SpanExporter(Protocol):
    def export(self, span: SpanRecord) -> None: ...


class InMemorySpanExporter:
    def __init__(self) -> None:
        self.spans: list[SpanRecord] = []
        self._lock = Lock()

    def export(self, span: SpanRecord) -> None:
        with self._lock:
            self.spans.append(span)


class Tracer:
    """Small vendor-neutral seam; an OTLP exporter can implement SpanExporter."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    @contextmanager
    def span(
        self,
        name: str,
        *,
        correlation: Correlation,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[None]:
        started = monotonic_ns()
        status = "ok"
        try:
            yield
        except Exception:
            status = "error"
            raise
        finally:
            combined = correlation.attributes()
            combined.update(attributes or {})
            self._exporter.export(
                SpanRecord(
                    trace_id=uuid4().hex,
                    span_id=uuid4().hex[:16],
                    name=name,
                    duration_ms=(monotonic_ns() - started) / 1_000_000,
                    status=status,
                    attributes=safe_attributes(combined),
                )
            )


class MetricRegistry:
    """Dependency-free counter/gauge registry with bounded, predeclared label names."""

    def __init__(self, *, allowed_labels: set[str]) -> None:
        self.allowed_labels = frozenset(allowed_labels)
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._lock = Lock()

    def add(self, name: str, value: float = 1, *, labels: Mapping[str, str] | None = None) -> None:
        if not _METRIC_NAME.fullmatch(name):
            raise ValueError("invalid metric name")
        normalized = tuple(sorted((labels or {}).items()))
        if not {key for key, _ in normalized} <= self.allowed_labels:
            raise ValueError("metric label is not allowlisted")
        if any(not _SAFE_ATTRIBUTE.fullmatch(value) for _, value in normalized):
            raise ValueError("metric label value is unsafe")
        key = (name, normalized)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._values.items()):
                suffix = ""
                if labels:
                    suffix = "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"
                lines.append(f"{name}{suffix} {value:g}")
        return "\n".join(lines) + ("\n" if lines else "")
