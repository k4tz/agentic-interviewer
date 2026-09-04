from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import platform
import subprocess
import sys
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

ANALYSIS_PROMPT = (
    "Evaluate the candidate response to the interview question. Return one raw JSON object with "
    "exactly disposition, confidence, feedback, and follow_up_question. disposition must be "
    "accept, follow_up, insufficient, gibberish, or skip. Use skip only when the candidate says "
    "they do not know or asks to move on. Use gibberish for incoherent word salad. Use "
    "insufficient for an intelligible relevant answer that is too thin. Use follow_up only when "
    "one concise neutral probe would obtain a specific missing detail. Use accept when the answer "
    "substantially addresses the question. confidence must be a JSON number from 0 through 1. "
    "feedback must be a JSON string. follow_up_question must be a JSON string only for follow_up "
    "and null otherwise. Do not include Markdown or commentary."
)
DISPOSITIONS = {"accept", "follow_up", "insufficient", "gibberish", "skip"}


def command_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_host_environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "gpu": command_output(
            [
                "nvidia-smi",
                "--query-gpu=name,compute_cap,driver_version,memory.total",
                "--format=csv,noheader",
            ]
        ),
        "docker_cli": command_output(["docker", "--version"]),
        "docker_server": command_output(
            ["docker", "version", "--format", "{{.Server.Version}}"]
        ),
    }


@dataclass
class Sample:
    ok: bool
    task_success: bool
    e2e_ms: float
    schema_valid: bool | None = None
    expected_label: str | None = None
    observed_label: str | None = None
    ttft_ms: float | None = None
    tpot_ms: float | None = None
    queue_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    input_audio_seconds: float = 0.0
    output_audio_seconds: float = 0.0
    transcription_ms: float | None = None
    reasoning_ms: float | None = None
    synthesis_ms: float | None = None
    orchestration_overhead_ms: float | None = None
    status: int | None = None
    error: str | None = None


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))
    return round(ordered[index], 3)


def metric_summary(samples: list[Sample], field: str) -> dict[str, float | None]:
    values = [float(value) for item in samples if (value := getattr(item, field)) is not None]
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 3) if values else None,
    }


def summarize(
    *,
    profile: str,
    concurrency: int,
    samples: list[Sample],
    wall_seconds: float,
    gpu_samples_mib: list[float],
    baseline_vram_mib: float,
) -> dict[str, Any]:
    successful = [item for item in samples if item.ok]
    peak_mib = max(gpu_samples_mib) if gpu_samples_mib else None
    allocated_gib = max(peak_mib - baseline_vram_mib, 0.0) / 1024 if peak_mib is not None else None
    allocated_gb = allocated_gib * 1.073741824 if allocated_gib is not None else None
    output_tokens = sum(item.output_tokens for item in successful)
    output_tps = output_tokens / wall_seconds if wall_seconds > 0 else 0.0
    input_audio = sum(item.input_audio_seconds for item in successful)
    output_audio = sum(item.output_audio_seconds for item in successful)
    schema_samples = [item for item in samples if item.schema_valid is not None]
    label_pairs = sorted(
        {
            (str(item.expected_label), str(item.observed_label))
            for item in samples
            if item.expected_label is not None
        }
    )
    return {
        "profile": profile,
        "concurrency": concurrency,
        "attempted": len(samples),
        "successful": len(successful),
        "failed": len(samples) - len(successful),
        "error_rate": round((len(samples) - len(successful)) / max(len(samples), 1), 6),
        "task_success_rate": round(
            sum(item.task_success for item in samples) / max(len(samples), 1), 6
        ),
        "schema_valid_rate": (
            round(sum(bool(item.schema_valid) for item in schema_samples) / len(schema_samples), 6)
            if schema_samples
            else None
        ),
        "label_pair_counts": {
            f"{expected}->{observed}": sum(
                item.expected_label == expected and str(item.observed_label) == observed
                for item in samples
            )
            for expected, observed in label_pairs
        },
        "wall_seconds": round(wall_seconds, 3),
        "requests_per_second": round(len(successful) / max(wall_seconds, 1e-9), 3),
        "sessions_per_minute": round(60 * len(successful) / max(wall_seconds, 1e-9), 3),
        "output_tokens": output_tokens,
        "output_tokens_per_second": round(output_tps, 3),
        "tokens_per_second_per_gb_vram": (
            round(output_tps / allocated_gb, 3) if allocated_gb else None
        ),
        "tokens_per_second_per_gib_vram": (
            round(output_tps / allocated_gib, 3) if allocated_gib else None
        ),
        "input_audio_seconds_per_wall_second": (
            round(input_audio / wall_seconds, 5) if wall_seconds > 0 else None
        ),
        "output_audio_seconds_per_wall_second": (
            round(output_audio / wall_seconds, 5) if wall_seconds > 0 else None
        ),
        "stt_aggregate_rtf": (
            round(
                sum((item.transcription_ms or 0) / 1000 for item in successful) / input_audio,
                5,
            )
            if input_audio > 0 and any(item.transcription_ms is not None for item in successful)
            else None
        ),
        "tts_aggregate_rtf": (
            round(
                sum((item.synthesis_ms or 0) / 1000 for item in successful) / output_audio,
                5,
            )
            if output_audio > 0 and any(item.synthesis_ms is not None for item in successful)
            else None
        ),
        "latency_ms": {
            "e2e": metric_summary(successful, "e2e_ms"),
            "ttft": metric_summary(successful, "ttft_ms"),
            "tpot": metric_summary(successful, "tpot_ms"),
            "queue": metric_summary(successful, "queue_ms"),
            "transcription": metric_summary(successful, "transcription_ms"),
            "reasoning": metric_summary(successful, "reasoning_ms"),
            "synthesis": metric_summary(successful, "synthesis_ms"),
            "orchestration_overhead": metric_summary(successful, "orchestration_overhead_ms"),
        },
        "gpu": {
            "baseline_mib": baseline_vram_mib,
            "sample_count": len(gpu_samples_mib),
            "minimum_used_mib": round(min(gpu_samples_mib), 1) if gpu_samples_mib else None,
            "peak_used_mib": round(peak_mib, 1) if peak_mib is not None else None,
            "peak_allocated_gib_above_baseline": (
                round(allocated_gib, 3) if allocated_gib is not None else None
            ),
            "peak_allocated_gb_above_baseline": (
                round(allocated_gb, 3) if allocated_gb is not None else None
            ),
        },
        "status_counts": {
            str(status): sum(item.status == status for item in samples)
            for status in sorted({item.status for item in samples if item.status is not None})
        },
        "errors": sorted({item.error for item in samples if item.error}),
    }


class NvidiaSampler:
    def __init__(self) -> None:
        self.samples_mib: list[float] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> NvidiaSampler:
        self._task = asyncio.create_task(self._sample())
        return self

    async def __aexit__(self, *_args: object) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                process = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await process.communicate()
                self.samples_mib.append(float(stdout.decode().strip().splitlines()[0]))
            except (OSError, ValueError, IndexError):
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.25)
            except TimeoutError:
                pass


def load_cases(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) < 20:
        raise ValueError("the reasoning workload must contain at least 20 cases")
    return cases


def parse_analysis(content: str) -> dict[str, Any] | None:
    stripped = content.strip()
    if stripped.startswith("```json\n") and stripped.endswith("\n```"):
        stripped = stripped[8:-4].strip()
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {
        "disposition",
        "confidence",
        "feedback",
        "follow_up_question",
    }:
        return None
    disposition = value["disposition"]
    follow_up = value["follow_up_question"]
    valid = (
        isinstance(disposition, str)
        and disposition in DISPOSITIONS
        and isinstance(value["confidence"], (int, float))
        and not isinstance(value["confidence"], bool)
        and 0 <= value["confidence"] <= 1
        and isinstance(value["feedback"], str)
        and (
            (disposition == "follow_up" and isinstance(follow_up, str) and bool(follow_up.strip()))
            or (disposition != "follow_up" and follow_up is None)
        )
    )
    return value if valid else None


def valid_analysis(content: str, expected: str) -> bool:
    value = parse_analysis(content)
    return value is not None and value["disposition"] == expected


async def reasoning_request(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    case: dict[str, str],
    max_tokens: int,
) -> Sample:
    started = time.perf_counter()
    first_token_at: float | None = None
    content_parts: list[str] = []
    usage: dict[str, Any] = {}
    server_metrics: dict[str, Any] = {}
    status: int | None = None
    try:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": ANALYSIS_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {case['question']}\nCandidate answer: {case['answer']}"
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as response:
            status = response.status_code
            if not response.is_success:
                body = (await response.aread()).decode(errors="replace")[:500]
                return Sample(
                    False,
                    False,
                    (time.perf_counter() - started) * 1000,
                    status=status,
                    error=body,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                if chunk.get("usage"):
                    usage = chunk["usage"]
                if chunk.get("metrics"):
                    server_metrics = chunk["metrics"]
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}) if choices else {}
                text = delta.get("content")
                if text:
                    first_token_at = first_token_at or time.perf_counter()
                    content_parts.append(text)
        finished = time.perf_counter()
        output_tokens = int(usage.get("completion_tokens") or 0)
        ttft_ms = (
            float(server_metrics.get("time_to_first_token_ms"))
            if server_metrics.get("time_to_first_token_ms") is not None
            else ((first_token_at - started) * 1000 if first_token_at else None)
        )
        tpot_ms = server_metrics.get("mean_itl_ms")
        if tpot_ms is None and ttft_ms is not None and output_tokens > 1:
            tpot_ms = ((finished - started) * 1000 - ttft_ms) / (output_tokens - 1)
        content = "".join(content_parts)
        parsed = parse_analysis(content)
        return Sample(
            ok=True,
            task_success=valid_analysis(content, case["expected_disposition"]),
            e2e_ms=(finished - started) * 1000,
            schema_valid=parsed is not None,
            expected_label=case["expected_disposition"],
            observed_label=str(parsed["disposition"]) if parsed else None,
            ttft_ms=ttft_ms,
            tpot_ms=float(tpot_ms) if tpot_ms is not None else None,
            queue_ms=(
                float(server_metrics["queue_time_ms"])
                if server_metrics.get("queue_time_ms") is not None
                else None
            ),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=output_tokens,
            reasoning_ms=(finished - started) * 1000,
            status=status,
        )
    except Exception as exc:
        return Sample(
            False,
            False,
            (time.perf_counter() - started) * 1000,
            status=status,
            error=f"{type(exc).__name__}: {exc}",
        )


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as source:
        return source.getnframes() / source.getframerate()


async def stt_request(
    client: httpx.AsyncClient, *, base_url: str, model: str, audio: bytes, index: int
) -> Sample:
    started = time.perf_counter()
    duration = wav_seconds(audio)
    try:
        response = await client.post(
            f"{base_url}/v1/audio/transcriptions",
            data={"model": model, "language": "en", "response_format": "json"},
            files={"file": (f"reference-{index}.wav", audio, "audio/wav")},
        )
        elapsed = (time.perf_counter() - started) * 1000
        text = response.json().get("text", "") if response.is_success else ""
        return Sample(
            response.is_success,
            response.is_success and len(text.split()) >= 5,
            elapsed,
            input_audio_seconds=duration,
            transcription_ms=elapsed,
            status=response.status_code,
            error=None if response.is_success else response.text[:500],
        )
    except Exception as exc:
        return Sample(False, False, (time.perf_counter() - started) * 1000, error=str(exc))


async def tts_request(
    client: httpx.AsyncClient, *, base_url: str, model: str, index: int
) -> Sample:
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{base_url}/v1/audio/speech",
            json={
                "model": model,
                "voice": "af_heart",
                "input": (
                    "Thanks for the example. Please explain the measurement and your personal "
                    f"contribution before we continue. Benchmark turn {index}."
                ),
                "response_format": "wav",
                "speed": 1.0,
            },
        )
        elapsed = (time.perf_counter() - started) * 1000
        audio_seconds = wav_seconds(response.content) if response.is_success else 0.0
        return Sample(
            response.is_success,
            response.is_success and audio_seconds > 0,
            elapsed,
            output_audio_seconds=audio_seconds,
            synthesis_ms=elapsed,
            status=response.status_code,
            error=None if response.is_success else response.text[:500],
        )
    except Exception as exc:
        return Sample(False, False, (time.perf_counter() - started) * 1000, error=str(exc))


async def create_pipeline_session(client: httpx.AsyncClient, *, base_url: str, index: int) -> str:
    response = await client.post(
        f"{base_url}/interviews",
        json={
            "job_title": "AI Application Engineer",
            "resume_text": (
                "Software engineer with three years of experience building retrieval augmented "
                "generation systems, evaluation pipelines, and production Python APIs."
            ),
            "job_details": (
                "Build and operate reliable conversational AI applications, measure quality and "
                "latency, and collaborate on production deployments."
            ),
            "candidate_id": f"benchmark-candidate-{index}-{uuid.uuid4().hex[:8]}",
            "planning_controls": {
                "introduction_questions": 1,
                "resume_questions": 1,
                "job_questions": 0,
                "max_company_questions": 0,
                "max_adaptive_questions": 1,
                "max_adaptive_per_base_question": 1,
                "max_total_questions": 3,
            },
        },
    )
    response.raise_for_status()
    interview_id = response.json()["interview_id"]
    approval = await client.post(
        f"{base_url}/interviews/{interview_id}/setup-approval",
        json={"reviewer": "benchmark", "reason": "fixed benchmark fixture"},
    )
    approval.raise_for_status()
    started = await client.post(f"{base_url}/interviews/{interview_id}/start")
    started.raise_for_status()
    return str(interview_id)


def latest_usage_by_capability(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in state.get("usage") or []:
        capability = item.get("capability")
        if capability in {"transcription", "reasoning", "synthesis"}:
            latest[capability] = item
    return latest


async def pipeline_request(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    audio: bytes,
    index: int,
    interview_id: str,
) -> Sample:
    started = time.perf_counter()
    status: int | None = None
    try:
        measured_at = time.perf_counter()
        answer = await client.post(
            f"{base_url}/interviews/{interview_id}/audio-answers",
            params={"audio_format": "wav", "language": "en"},
            headers={
                "Idempotency-Key": f"benchmark-answer-{index}-{uuid.uuid4().hex}",
                "Content-Type": "application/octet-stream",
            },
            content=audio,
        )
        status = answer.status_code
        if not answer.is_success:
            return Sample(
                False,
                False,
                (time.perf_counter() - measured_at) * 1000,
                input_audio_seconds=wav_seconds(audio),
                status=status,
                error=answer.text[:500],
            )
        state_after_answer = answer.json()
        speech = await client.post(
            f"{base_url}/interviews/{interview_id}/speech",
            params={"output_format": "wav"},
            headers={"Idempotency-Key": f"benchmark-speech-{index}-{uuid.uuid4().hex}"},
        )
        status = speech.status_code
        if not speech.is_success:
            return Sample(
                False,
                False,
                (time.perf_counter() - measured_at) * 1000,
                input_audio_seconds=wav_seconds(audio),
                status=status,
                error=speech.text[:500],
            )
        current = await client.get(f"{base_url}/interviews/{interview_id}")
        current.raise_for_status()
        finished = time.perf_counter()
        usage = latest_usage_by_capability(current.json())
        service_latency_ms = sum(
            float(item.get("total_latency_ms") or 0) for item in usage.values()
        )
        reasoning = usage.get("reasoning", {})
        transcription = usage.get("transcription", {})
        synthesis = usage.get("synthesis", {})
        transcription_ms = float(transcription.get("total_latency_ms") or 0)
        reasoning_ms = float(reasoning.get("total_latency_ms") or 0)
        synthesis_ms = float(synthesis.get("total_latency_ms") or 0)
        output_audio_seconds = wav_seconds(speech.content)
        last_response = str(state_after_answer.get("last_response") or "").strip()
        required_capabilities = {"transcription", "reasoning", "synthesis"}
        e2e_ms = (finished - measured_at) * 1000
        return Sample(
            ok=True,
            task_success=(
                bool(last_response)
                and output_audio_seconds > 0
                and required_capabilities.issubset(usage)
            ),
            e2e_ms=e2e_ms,
            ttft_ms=(
                float(reasoning["time_to_first_result_ms"])
                if reasoning.get("time_to_first_result_ms") is not None
                else None
            ),
            queue_ms=float(
                sum(float(item.get("queue_ms") or 0) for item in usage.values())
            ),
            input_tokens=int(reasoning.get("input_tokens") or 0),
            output_tokens=int(reasoning.get("output_tokens") or 0),
            input_audio_seconds=float(
                transcription.get("input_audio_seconds") or wav_seconds(audio)
            ),
            output_audio_seconds=float(
                synthesis.get("output_audio_seconds") or output_audio_seconds
            ),
            transcription_ms=transcription_ms,
            reasoning_ms=reasoning_ms,
            synthesis_ms=synthesis_ms,
            orchestration_overhead_ms=max(e2e_ms - service_latency_ms, 0.0),
            status=status,
        )
    except Exception as exc:
        return Sample(
            False,
            False,
            (time.perf_counter() - started) * 1000,
            input_audio_seconds=wav_seconds(audio),
            status=status,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_level(
    *,
    profile: str,
    concurrency: int,
    requests: int,
    baseline_vram_mib: float,
    operation: Callable[[int], Awaitable[Sample]],
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def admitted(index: int) -> Sample:
        async with semaphore:
            return await operation(index)

    async with NvidiaSampler() as gpu:
        started = time.perf_counter()
        samples = await asyncio.gather(*(admitted(index) for index in range(requests)))
        wall_seconds = time.perf_counter() - started
    result = summarize(
        profile=profile,
        concurrency=concurrency,
        samples=list(samples),
        wall_seconds=wall_seconds,
        gpu_samples_mib=gpu.samples_mib,
        baseline_vram_mib=baseline_vram_mib,
    )
    result["samples"] = [asdict(item) for item in samples]
    return result


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=max(args.concurrency) * 2 + 4)
    timeout = httpx.Timeout(args.timeout)
    cases = load_cases(args.workload) if args.profile == "reasoning" else []
    audio = args.audio_fixture.read_bytes() if args.profile in {"stt", "pipeline"} else b""
    results: list[dict[str, Any]] = []
    pipeline_sessions: dict[int, str] = {}
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        if args.profile == "reasoning":

            async def operation(index: int) -> Sample:
                return await reasoning_request(
                    client,
                    base_url=args.base_url,
                    model=args.model,
                    case=cases[index % len(cases)],
                    max_tokens=args.max_tokens,
                )

        elif args.profile == "stt":

            async def operation(index: int) -> Sample:
                return await stt_request(
                    client,
                    base_url=args.base_url,
                    model=args.model,
                    audio=audio,
                    index=index,
                )

        elif args.profile == "tts":

            async def operation(index: int) -> Sample:
                return await tts_request(
                    client, base_url=args.base_url, model=args.model, index=index
                )

        else:

            async def operation(index: int) -> Sample:
                return await pipeline_request(
                    client,
                    base_url=args.base_url,
                    audio=audio,
                    index=index,
                    interview_id=pipeline_sessions[index],
                )

        for index in range(args.warmup_requests):
            if args.profile == "pipeline":
                pipeline_sessions[index] = await create_pipeline_session(
                    client, base_url=args.base_url, index=-(index + 1)
                )
            await operation(index)
        for concurrency in args.concurrency:
            if args.profile == "pipeline":
                created = await asyncio.gather(
                    *(
                        create_pipeline_session(
                            client,
                            base_url=args.base_url,
                            index=(concurrency * 100_000) + index,
                        )
                        for index in range(args.requests)
                    )
                )
                pipeline_sessions = dict(enumerate(created))
            results.append(
                await run_level(
                    profile=args.profile,
                    concurrency=concurrency,
                    requests=args.requests,
                    baseline_vram_mib=args.baseline_vram_mib,
                    operation=operation,
                )
            )
    return {
        "standard": "GenAI Agentic Load Testing Standard 1.0",
        "phase": args.phase,
        "profile": args.profile,
        "model": args.model,
        "quantization": args.quantization,
        "engine_image": args.engine_image,
        "engine_runner": args.engine_runner,
        "max_model_len": args.max_model_len,
        "max_num_seqs": args.max_num_seqs,
        "gpu_memory_utilization": args.gpu_memory_utilization,
        "base_url": args.base_url,
        "concurrency_ladder": args.concurrency,
        "requests_per_level": args.requests,
        "warmup_requests_discarded": args.warmup_requests,
        "workload": str(args.workload) if args.profile == "reasoning" else None,
        "workload_sha256": (
            sha256_file(args.workload) if args.profile == "reasoning" else None
        ),
        "audio_fixture": (
            str(args.audio_fixture) if args.profile in {"stt", "pipeline"} else None
        ),
        "audio_fixture_sha256": (
            sha256_file(args.audio_fixture)
            if args.profile in {"stt", "pipeline"}
            else None
        ),
        "generated_at_epoch": time.time(),
        "host_environment": capture_host_environment(),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standard v1 service benchmark harness")
    parser.add_argument(
        "--profile", choices=("reasoning", "stt", "tts", "pipeline"), required=True
    )
    parser.add_argument("--phase", choices=("isolated-baseline", "isolated-sweep", "combined"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", required=True)
    parser.add_argument("--quantization", default="not-applicable")
    parser.add_argument("--engine-image", default="vllm/vllm-openai:v0.26.0")
    parser.add_argument("--engine-runner", default="V1-on-WSL2")
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--max-num-seqs", type=int, default=16)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--warmup-requests", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--baseline-vram-mib", type=float, default=0.0)
    parser.add_argument(
        "--workload",
        type=Path,
        default=Path("evaluation/fixtures/reasoning_conversations_v1.json"),
    )
    parser.add_argument(
        "--audio-fixture",
        type=Path,
        default=Path("evaluation/fixtures/reference_interview_answer.wav"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase is None:
        args.phase = "isolated-baseline" if args.concurrency == [1] else "isolated-sweep"
    if any(level < 1 for level in args.concurrency):
        parser.error("concurrency values must be positive")
    if args.requests < max(args.concurrency):
        parser.error("requests must be at least the maximum concurrency")
    return args


async def main() -> int:
    args = parse_args()
    report = await benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
