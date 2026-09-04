import json
from dataclasses import asdict

import pytest

from scripts.standard_benchmark import Sample, parse_analysis, percentile, summarize, valid_analysis
from scripts.validate_standard_results import audit_report


def summary(samples, *, wall_seconds=4.0, gpu_samples_mib=None):
    return summarize(
        profile="reasoning",
        concurrency=2,
        samples=samples,
        wall_seconds=wall_seconds,
        gpu_samples_mib=gpu_samples_mib or [],
        baseline_vram_mib=1024,
    )


def test_failures_do_not_count_as_successful_throughput():
    result = summary(
        [
            Sample(True, True, 1000, output_tokens=20, status=200),
            Sample(True, False, 3000, output_tokens=30, status=200),
            Sample(False, False, 10000, output_tokens=999, status=504, error="timeout"),
        ],
        gpu_samples_mib=[1536, 2048],
    )
    assert result["successful"] == 2
    assert result["failed"] == 1
    assert result["output_tokens"] == 50
    assert result["output_tokens_per_second"] == 12.5
    assert result["requests_per_second"] == 0.5
    assert result["sessions_per_minute"] == 30
    assert result["task_success_rate"] == 0.333333
    assert result["latency_ms"]["e2e"]["p95"] == 3000
    assert result["status_counts"] == {"200": 2, "504": 1}
    assert result["tokens_per_second_per_gib_vram"] == 12.5
    assert result["tokens_per_second_per_gb_vram"] == round(12.5 / 1.073741824, 3)


def test_latency_rtf_is_not_reciprocal_of_parallel_audio_throughput():
    result = summary(
        [
            Sample(True, True, 1000, input_audio_seconds=10, transcription_ms=1000),
            Sample(True, True, 1000, input_audio_seconds=10, transcription_ms=1000),
        ],
        wall_seconds=1,
    )
    assert result["stt_aggregate_rtf"] == 0.1
    assert result["input_audio_seconds_per_wall_second"] == 20
    assert result["stt_aggregate_rtf"] != 1 / result["input_audio_seconds_per_wall_second"]


def test_missing_gpu_telemetry_does_not_invent_normalized_throughput():
    result = summary([Sample(True, True, 1000, output_tokens=20)])
    assert result["gpu"]["peak_used_mib"] is None
    assert result["tokens_per_second_per_gb_vram"] is None


def test_nearest_rank_percentile_discloses_small_sample_tail():
    assert percentile(list(range(1, 21)), 0.95) == 19
    assert percentile(list(range(1, 21)), 0.99) == 20
    assert percentile([], 0.95) is None


@pytest.mark.parametrize("disposition", [[], {}, True, 12, "unknown"])
def test_invalid_dispositions_are_schema_failures_not_parser_crashes(disposition):
    content = json.dumps(
        {
            "disposition": disposition,
            "confidence": 0.9,
            "feedback": "test",
            "follow_up_question": None,
        }
    )
    assert parse_analysis(content) is None


def test_schema_validity_does_not_imply_correct_disposition():
    content = json.dumps(
        {
            "disposition": "accept",
            "confidence": 0.9,
            "feedback": "test",
            "follow_up_question": None,
        }
    )
    assert parse_analysis(content) is not None
    assert valid_analysis(content, "accept")
    assert not valid_analysis(content, "follow_up")


def test_followup_requires_a_nonempty_question():
    content = json.dumps(
        {
            "disposition": "follow_up",
            "confidence": 0.9,
            "feedback": "test",
            "follow_up_question": " ",
        }
    )
    assert parse_analysis(content) is None


def saved_report():
    samples = [Sample(True, True, 1000, output_tokens=20, status=200) for _ in range(20)]
    row = summary(samples)
    row["samples"] = [asdict(sample) for sample in samples]
    return {
        "profile": "reasoning",
        "requests_per_level": 20,
        "warmup_requests_discarded": 5,
        "concurrency_ladder": [2],
        "results": [row],
    }


def test_saved_artifact_audit_accepts_reproducible_arithmetic():
    assert audit_report(saved_report()) == []


def test_saved_artifact_audit_rejects_tampered_throughput():
    report = saved_report()
    report["results"][0]["output_tokens_per_second"] *= 2
    assert any("output_tokens_per_second" in error for error in audit_report(report))
