from decimal import Decimal

import pytest

from agentic_interviewer.domain.models import NormalizedUsage
from agentic_interviewer.operations import (
    AdmissionController,
    AdmissionRejected,
    BudgetExceeded,
    BudgetLimits,
    CircuitBreaker,
    CircuitState,
    PriorityWorkQueue,
    RateLimiter,
    SessionBudget,
)


def usage(**values):
    return NormalizedUsage(provider="fake", model="v1", capability="reasoning", **values)


def limits():
    return BudgetLimits(
        input_tokens=100,
        output_tokens=50,
        audio_seconds=10,
        synthesized_characters=1000,
        estimated_cost_usd=Decimal("1"),
    )


def test_budget_warns_at_threshold_and_rejects_without_mutating_state():
    budget = SessionBudget(limits())
    warning = budget.record(usage(input_tokens=80))
    assert warning.warning_dimensions == ("input_tokens",)
    with pytest.raises(BudgetExceeded) as exc:
        budget.record(usage(input_tokens=21))
    assert exc.value.dimensions == ("input_tokens",)
    assert budget.snapshot().state.input_tokens == 80


def test_budget_allows_exact_limit():
    budget = SessionBudget(limits())
    snapshot = budget.record(usage(input_tokens=100))
    assert snapshot.state.input_tokens == 100


def test_rate_limit_is_deterministic_and_reports_retry_after():
    now = [10.0]
    limiter = RateLimiter(capacity=2, refill_per_second=1, clock=lambda: now[0])
    limiter.acquire("tenant")
    limiter.acquire("tenant")
    with pytest.raises(AdmissionRejected) as exc:
        limiter.acquire("tenant")
    assert exc.value.reason == "rate_limit"
    assert exc.value.retry_after_seconds == pytest.approx(1)
    now[0] += 1
    limiter.acquire("tenant")


def test_concurrency_limits_are_released_even_when_work_fails():
    controller = AdmissionController(max_concurrent=2, max_per_tenant=1)
    with controller.lease("a"):
        with pytest.raises(AdmissionRejected) as exc:
            with controller.lease("a"):
                pass
        assert exc.value.reason == "tenant_concurrency"
    with pytest.raises(RuntimeError):
        with controller.lease("a"):
            raise RuntimeError("worker failed")
    with controller.lease("a"):
        pass


def test_circuit_breaker_opens_and_allows_one_recovery_probe():
    now = [0.0]
    circuit = CircuitBreaker(failure_threshold=2, recovery_seconds=5, clock=lambda: now[0])
    circuit.record_failure()
    circuit.record_failure()
    assert circuit.state == CircuitState.OPEN
    with pytest.raises(AdmissionRejected, match="circuit_open"):
        with circuit.lease():
            pass
    now[0] = 5
    assert circuit.state == CircuitState.HALF_OPEN
    with circuit.lease():
        pass
    assert circuit.state == CircuitState.CLOSED


def test_priority_queue_is_bounded_and_preserves_fifo_within_priority():
    queue = PriorityWorkQueue[str](max_depth=3)
    queue.put("batch", priority="batch")
    queue.put("live-1", priority="live")
    queue.put("live-2", priority="live")
    with pytest.raises(AdmissionRejected, match="queue_full"):
        queue.put("overflow", priority="normal")
    assert [queue.get(), queue.get(), queue.get()] == ["live-1", "live-2", "batch"]
