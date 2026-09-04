from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from pydantic import BaseModel, Field

from agentic_interviewer.policy import GuardrailPolicy


class GuardrailFixture(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    text: str
    expected_intent: str
    expected_action: str


@dataclass(frozen=True)
class FixtureResult:
    id: str
    passed: bool
    actual_intent: str
    actual_action: str
    latency_ms: float


@dataclass(frozen=True)
class EvaluationReport:
    suite: str
    policy_version: str
    total: int
    passed: int
    pass_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    results: tuple[FixtureResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]


def evaluate_guardrails(path: Path) -> EvaluationReport:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fixtures = [GuardrailFixture.model_validate(item) for item in raw["cases"]]
    if not fixtures:
        raise ValueError("evaluation suite must contain at least one case")
    policy = GuardrailPolicy()
    results: list[FixtureResult] = []
    for fixture in fixtures:
        started = perf_counter_ns()
        decision = policy.classify(fixture.text)
        latency_ms = (perf_counter_ns() - started) / 1_000_000
        actual_intent = decision.primary_intent.value
        results.append(
            FixtureResult(
                id=fixture.id,
                passed=(
                    actual_intent == fixture.expected_intent
                    and decision.proposed_action == fixture.expected_action
                ),
                actual_intent=actual_intent,
                actual_action=decision.proposed_action,
                latency_ms=latency_ms,
            )
        )
    latencies = [item.latency_ms for item in results]
    passed = sum(item.passed for item in results)
    return EvaluationReport(
        suite=raw.get("suite", path.stem),
        policy_version=policy.version,
        total=len(results),
        passed=passed,
        pass_rate=passed / len(results),
        latency_p50_ms=statistics.median(latencies),
        latency_p95_ms=_percentile(latencies, 0.95),
        results=tuple(results),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic offline evaluations")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    args = parser.parse_args()
    report = evaluate_guardrails(args.fixture)
    serialized = json.dumps(report.to_dict(), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if report.pass_rate >= args.min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
