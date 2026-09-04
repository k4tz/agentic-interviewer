"""Audit saved calibration artifacts without calling providers or rewriting raw results."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from .standard_benchmark import Sample, summarize
else:
    from standard_benchmark import Sample, summarize


def audit_report(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("requests_per_level") != 20:
        errors.append("expected 20 measured requests per level")
    if payload.get("warmup_requests_discarded") != 5:
        errors.append("expected five discarded warmups")
    results = payload.get("results", [])
    if [row["concurrency"] for row in results] != payload.get("concurrency_ladder"):
        errors.append("result levels differ from declared ladder")
    for row in results:
        label = f"concurrency={row['concurrency']}"
        samples = [Sample(**sample) for sample in row["samples"]]
        if len(samples) != payload["requests_per_level"]:
            errors.append(f"{label}: raw sample count mismatch")
        peak = row["gpu"]["peak_used_mib"]
        recomputed = summarize(
            profile=payload["profile"],
            concurrency=row["concurrency"],
            samples=samples,
            wall_seconds=row["wall_seconds"],
            gpu_samples_mib=[peak] if peak is not None else [],
            baseline_vram_mib=row["gpu"]["baseline_mib"],
        )
        for key in (
            "attempted",
            "successful",
            "failed",
            "error_rate",
            "task_success_rate",
            "schema_valid_rate",
            "label_pair_counts",
            "output_tokens",
            "latency_ms",
            "status_counts",
            "errors",
            "stt_aggregate_rtf",
            "tts_aggregate_rtf",
        ):
            if row[key] != recomputed[key]:
                errors.append(f"{label}: {key} differs from raw samples")
        for key in (
            "requests_per_second",
            "sessions_per_minute",
            "output_tokens_per_second",
            "tokens_per_second_per_gb_vram",
            "tokens_per_second_per_gib_vram",
            "input_audio_seconds_per_wall_second",
            "output_audio_seconds_per_wall_second",
        ):
            actual, expected = row[key], recomputed[key]
            # Original wall time is stored rounded to milliseconds; allow its rounding error.
            valid = (
                actual == expected
                if actual is None or expected is None
                else math.isclose(actual, expected, rel_tol=0.001, abs_tol=0.01)
            )
            if not valid:
                errors.append(f"{label}: {key} differs from raw samples")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    for profile in ("reasoning-q4", "reasoning-q8", "stt", "tts", "pipeline"):
        for phase in ("baseline", "sweep"):
            name = f"{profile}-{phase}.json"
            path = args.directory / name
            if not path.is_file():
                errors.append(f"missing artifact: {name}")
                continue
            raw = path.read_bytes()
            payload = json.loads(raw)
            findings = audit_report(payload)
            expected_ladder = [1] if phase == "baseline" else [1, 2, 4, 8, 16]
            if payload["concurrency_ladder"] != expected_ladder:
                findings.append("unexpected ladder")
            for field in ("workload", "audio_fixture"):
                if payload.get(field):
                    fixture = project / payload[field].replace("\\", "/")
                    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
                    if digest != payload[f"{field}_sha256"]:
                        findings.append(f"{field} hash mismatch")
            errors.extend(f"{name}: {finding}" for finding in findings)
            files.append(
                {
                    "file": name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "points": len(payload["results"]),
                    "measured_requests": sum(row["attempted"] for row in payload["results"]),
                    "successful_requests": sum(row["successful"] for row in payload["results"]),
                    "integrity_pass": not findings,
                }
            )
    audit = {
        "audited_at_utc": datetime.now(UTC).isoformat(),
        "scope": "Saved-artifact integrity, not a new inference run or production certification",
        "raw_artifacts_modified": False,
        "files": files,
        "total_measured_requests": sum(item["measured_requests"] for item in files),
        "integrity_pass": not errors,
        "errors": errors,
        "interpretation": {
            "q4_q8_quality_gate": "failed: 70% labeled correctness and 90% schema validity",
            "combined_highest_error_free_tested_concurrency": 4,
            "combined_slo_qualified_concurrency": None,
            "production_certified": False,
            "notes": [
                "Concurrency four fails the overall p50 and STT p95 proposals.",
                "HTTP turn latency excludes browser/VAD and is not streaming first-audio latency.",
                "Legacy aggregate_rtf fields sum request latency/audio duration, not wall/audio.",
                "Legacy sessions_per_minute counts requests or turns, not whole interviews.",
                "Speech-only engine fields inherited reasoning CLI defaults; use environment.json.",
                "No sustained certification or new Q8 run was performed by this audit.",
            ],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
