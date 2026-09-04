from pathlib import Path

from agentic_interviewer.evaluation.harness import evaluate_guardrails


def test_offline_guardrail_release_gate_passes():
    fixture = Path(__file__).parents[2] / "evaluation" / "fixtures" / "guardrails.json"
    report = evaluate_guardrails(fixture)
    assert report.total == 9
    assert report.passed == report.total
    assert report.pass_rate == 1
    assert report.latency_p95_ms >= 0
