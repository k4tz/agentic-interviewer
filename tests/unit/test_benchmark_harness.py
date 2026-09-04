from pathlib import Path

from agentic_interviewer.evaluation.benchmark import run_fake_benchmark


async def test_fake_benchmark_has_exact_normalized_usage():
    fixture = (
        Path(__file__).parents[2] / "evaluation" / "fixtures" / "fake_reasoning_benchmark.json"
    )
    report = await run_fake_benchmark(fixture)
    assert report.adapter == "fake/deterministic-v1"
    assert report.iterations == 100
    assert report.total_input_tokens == 600
    assert report.total_output_tokens == 600
    assert report.latency_p95_ms >= 0
