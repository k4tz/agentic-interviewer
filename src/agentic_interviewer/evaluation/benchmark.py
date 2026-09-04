from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter_ns

from pydantic import BaseModel, Field

from agentic_interviewer.adapters.fake import FakeUnifiedAdapter
from agentic_interviewer.domain.models import ExecutionControls, ReasoningRequest


class BenchmarkFixture(BaseModel):
    name: str
    iterations: int = Field(gt=0, le=10_000)
    prompt: str = Field(min_length=1)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


@dataclass(frozen=True)
class BenchmarkReport:
    name: str
    adapter: str
    iterations: int
    total_input_tokens: int
    total_output_tokens: int
    latency_p50_ms: float
    latency_p95_ms: float
    throughput_calls_per_second: float


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]


async def run_fake_benchmark(path: Path) -> BenchmarkReport:
    fixture = BenchmarkFixture.model_validate_json(path.read_text(encoding="utf-8"))
    adapter = FakeUnifiedAdapter()
    latencies: list[float] = []
    total_input = 0
    total_output = 0
    benchmark_started = perf_counter_ns()
    for index in range(fixture.iterations):
        request = ReasoningRequest(
            task="render_question",
            prompt=fixture.prompt,
            controls=ExecutionControls(
                idempotency_key=f"benchmark-{index}",
                max_input_tokens=fixture.max_input_tokens,
                max_output_tokens=fixture.max_output_tokens,
            ),
        )
        started = perf_counter_ns()
        result = await adapter.generate(request)
        latencies.append((perf_counter_ns() - started) / 1_000_000)
        total_input += result.usage.input_tokens or 0
        total_output += result.usage.output_tokens or 0
    elapsed_seconds = max((perf_counter_ns() - benchmark_started) / 1_000_000_000, 1e-9)
    return BenchmarkReport(
        name=fixture.name,
        adapter=f"{adapter.capabilities.provider}/{adapter.capabilities.model}",
        iterations=fixture.iterations,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        latency_p50_ms=statistics.median(latencies),
        latency_p95_ms=_percentile(latencies, 0.95),
        throughput_calls_per_second=fixture.iterations / elapsed_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline fake-adapter smoke benchmark")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_fake_benchmark(args.fixture))
    serialized = json.dumps(asdict(report), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
