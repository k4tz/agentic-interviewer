from __future__ import annotations

import heapq
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from threading import Lock
from time import monotonic

from agentic_interviewer.domain.models import BudgetState, NormalizedUsage


class BudgetExceeded(RuntimeError):
    def __init__(self, dimensions: tuple[str, ...]) -> None:
        super().__init__(f"session budget exceeded: {', '.join(dimensions)}")
        self.dimensions = dimensions


class AdmissionRejected(RuntimeError):
    def __init__(self, reason: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class BudgetLimits:
    input_tokens: int
    output_tokens: int
    audio_seconds: float
    synthesized_characters: int
    estimated_cost_usd: Decimal
    warning_ratio: Decimal = Decimal("0.8")

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.audio_seconds,
            self.synthesized_characters,
            self.estimated_cost_usd,
        )
        if any(value <= 0 for value in values):
            raise ValueError("all budget limits must be positive")
        if not Decimal("0") < self.warning_ratio < Decimal("1"):
            raise ValueError("warning_ratio must be between zero and one")


@dataclass(frozen=True)
class BudgetSnapshot:
    state: BudgetState
    warning_dimensions: tuple[str, ...]


class SessionBudget:
    """Atomically checks cumulative normalized usage before committing it."""

    def __init__(self, limits: BudgetLimits) -> None:
        self.limits = limits
        self._state = BudgetState()
        self._lock = Lock()

    def record(self, usage: NormalizedUsage) -> BudgetSnapshot:
        with self._lock:
            candidate = self._state.model_copy(deep=True)
            candidate.input_tokens += usage.input_tokens or 0
            candidate.output_tokens += usage.output_tokens or 0
            candidate.input_audio_seconds += usage.input_audio_seconds or 0
            candidate.synthesized_characters += usage.synthesized_characters or 0
            candidate.estimated_cost_usd += Decimal(usage.estimated_cost_usd)
            exceeded = self._exceeded_dimensions(candidate)
            if exceeded:
                raise BudgetExceeded(exceeded)
            self._state = candidate
            return BudgetSnapshot(
                state=candidate.model_copy(deep=True),
                warning_dimensions=self._dimensions(candidate, self.limits.warning_ratio),
            )

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            state = self._state.model_copy(deep=True)
            return BudgetSnapshot(state, self._dimensions(state, self.limits.warning_ratio))

    def _dimensions(self, state: BudgetState, ratio: Decimal) -> tuple[str, ...]:
        checks = self._ratios(state)
        return tuple(name for name, consumed in checks.items() if consumed >= ratio)

    def _exceeded_dimensions(self, state: BudgetState) -> tuple[str, ...]:
        return tuple(name for name, consumed in self._ratios(state).items() if consumed > 1)

    def _ratios(self, state: BudgetState) -> dict[str, Decimal]:
        return {
            "input_tokens": Decimal(state.input_tokens) / self.limits.input_tokens,
            "output_tokens": Decimal(state.output_tokens) / self.limits.output_tokens,
            "audio_seconds": Decimal(str(state.input_audio_seconds))
            / Decimal(str(self.limits.audio_seconds)),
            "synthesized_characters": Decimal(state.synthesized_characters)
            / self.limits.synthesized_characters,
            "estimated_cost_usd": state.estimated_cost_usd / self.limits.estimated_cost_usd,
        }


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """In-memory token bucket. Production callers should replace it with a shared backend."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("rate limit capacity and refill rate must be positive")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def acquire(self, key: str, *, tokens: int = 1) -> None:
        if not key or tokens <= 0 or tokens > self.capacity:
            raise ValueError("key and token count must be valid")
        with self._lock:
            now = self._clock()
            bucket = self._buckets.setdefault(key, _Bucket(float(self.capacity), now))
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(self.capacity), bucket.tokens + elapsed * self.refill_per_second
            )
            bucket.updated_at = now
            if bucket.tokens < tokens:
                retry_after = (tokens - bucket.tokens) / self.refill_per_second
                raise AdmissionRejected("rate_limit", retry_after_seconds=retry_after)
            bucket.tokens -= tokens


class AdmissionController:
    """Fail-fast global and per-tenant concurrency admission primitive."""

    def __init__(self, *, max_concurrent: int, max_per_tenant: int) -> None:
        if max_concurrent <= 0 or max_per_tenant <= 0:
            raise ValueError("concurrency limits must be positive")
        self.max_concurrent = max_concurrent
        self.max_per_tenant = max_per_tenant
        self._active = 0
        self._by_tenant: dict[str, int] = {}
        self._lock = Lock()

    @contextmanager
    def lease(self, tenant_key: str) -> Iterator[None]:
        self._admit(tenant_key)
        try:
            yield
        finally:
            self._release(tenant_key)

    def _admit(self, tenant_key: str) -> None:
        if not tenant_key:
            raise ValueError("tenant_key is required")
        with self._lock:
            if self._active >= self.max_concurrent:
                raise AdmissionRejected("global_concurrency")
            if self._by_tenant.get(tenant_key, 0) >= self.max_per_tenant:
                raise AdmissionRejected("tenant_concurrency")
            self._active += 1
            self._by_tenant[tenant_key] = self._by_tenant.get(tenant_key, 0) + 1

    def _release(self, tenant_key: str) -> None:
        with self._lock:
            self._active -= 1
            remaining = self._by_tenant[tenant_key] - 1
            if remaining:
                self._by_tenant[tenant_key] = remaining
            else:
                del self._by_tenant[tenant_key]


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe provider circuit breaker with a single half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold <= 0 or recovery_seconds <= 0:
            raise ValueError("circuit limits must be positive")
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._opened_at is None:
                return CircuitState.CLOSED
            if self._clock() - self._opened_at >= self.recovery_seconds:
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN

    @contextmanager
    def lease(self) -> Iterator[None]:
        with self._lock:
            if self._opened_at is not None:
                if self._clock() - self._opened_at < self.recovery_seconds:
                    raise AdmissionRejected("provider_circuit_open")
                if self._half_open_in_flight:
                    raise AdmissionRejected("provider_circuit_half_open")
                self._half_open_in_flight = True
        try:
            yield
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._half_open_in_flight = False
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()


class PriorityWorkQueue[T]:
    """Bounded deterministic queue; distributed deployments need an equivalent broker."""

    _PRIORITY = {"live": 0, "normal": 1, "batch": 2}

    def __init__(self, *, max_depth: int) -> None:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        self.max_depth = max_depth
        self._sequence = 0
        self._items: list[tuple[int, int, T]] = []
        self._lock = Lock()

    def put(self, item: T, *, priority: str) -> None:
        if priority not in self._PRIORITY:
            raise ValueError("unsupported priority")
        with self._lock:
            if len(self._items) >= self.max_depth:
                raise AdmissionRejected("queue_full")
            self._sequence += 1
            heapq.heappush(self._items, (self._PRIORITY[priority], self._sequence, item))

    def get(self) -> T:
        with self._lock:
            if not self._items:
                raise IndexError("queue is empty")
            return heapq.heappop(self._items)[2]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
