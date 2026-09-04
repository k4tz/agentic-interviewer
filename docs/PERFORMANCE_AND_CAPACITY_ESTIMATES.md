# Performance metrics and capacity estimates

## Status

This document establishes the metrics contract and capacity model. The former llama.cpp/Qwen
snapshots have been retired because they did not follow the current concurrency and normalization
standard. The vLLM Q4/Q8 short calibration is complete; see the
[`measured report`](reports/LOAD_TEST_REPORT_VLLM_2026-09-03.md) and
[`reproduction procedure`](VLLM_LOAD_TESTING.md). Production hardware ranges remain estimates until
the same workload is sustained on the target host. The historical measured stack used Speaches with
`Systran/faster-distil-whisper-small.en` for STT and
`speaches-ai/Kokoro-82M-v1.0-ONNX` for TTS, plus same-family GPTQ W4A16 and W8A16 Mistral 7B
reasoning variants served by vLLM.

The campaign's raw JSON and generated artifacts were deleted at the user's request on 2026-09-04.
Only the historical report remains. The supplied local configuration is reproducible test input,
not a statement that these containers or models are currently running; new runs create new evidence.

Speaches confirms that it uses faster-whisper for STT and Kokoro/Piper for TTS and supports realtime
and streaming APIs ([project documentation](https://github.com/speaches-ai/speaches)). The
[faster-whisper project](https://github.com/SYSTRAN/faster-whisper) reports up to four times OpenAI
Whisper speed in its published setup, but model, hardware, audio, batch size, and word-error-rate
settings materially affect results. Those public figures justify ranges, not a claim about this PC.

## Metric definitions

| Metric | Definition | Why it matters |
|---|---|---|
| WebSocket connect p95 | Browser connect start to admitted downstream session | Join reliability and provider reachability |
| VAD endpoint delay | Last candidate speech frame to committed turn boundary | Fixed conversational pause before STT can finish |
| STT real-time factor (RTF) | STT processing seconds / admitted audio seconds | `<1` is faster than real time; determines speech capacity |
| STT finalization | Commit to final transcript received | Candidate-visible stage latency |
| Reasoning queue time | Admitted request to model execution start | First saturation signal |
| Reasoning TTFT | Execution start to first generated token | Perceived responsiveness when streaming is used |
| Reasoning decode rate | Generated tokens / decode seconds | Capacity input, not an end-to-end SLO |
| Schema-valid rate | Valid, admitted structured outputs / model calls | Fast malformed output is a failure |
| TTS TTFA | TTS request to first playable audio byte | Determines conversational hand-off |
| TTS RTF | Synthesis seconds / output audio seconds | Speech generation capacity |
| Turn transition | Last candidate speech to first interviewer audio | Primary candidate-visible latency |
| Turn-decision quality | Correct accept/follow-up/retry/skip against labeled transcripts | Prevents latency optimization from degrading behavior |
| Effective throughput | Completed accepted turns or interviews per minute at SLO | Capacity planning outcome |

Also record p50/p95/p99, error/timeout/fallback rate, nudge rate, manual-commit rate, transcript
correction rate, unusable-answer retry rate, question-skip rate, prompt/output tokens, cache hits,
GPU/CPU/RAM/VRAM utilization, active/deferred requests, queue depth and oldest age. Do not use names,
transcripts, tenant IDs, or interview IDs as metric labels.

## Local-development measurement status

The 2026-09-03 campaign measured isolated reasoning, STT, and TTS, then the combined candidate-turn
path at concurrency `1, 2, 4, 8, 16`. It discarded five warm-ups per profile, recorded 20 raw
requests per level, treated structured-output correctness as a capacity gate, and reported
aggregate output tok/s, tok/s/decimal-GB, and tok/s/GiB of VRAM allocated above the pre-start
baseline.

| Measured point | Result | Admission interpretation |
|---|---:|---|
| Q4 reasoning, concurrency 16 | 741.9 output tok/s; 48.7 tok/s/GB; p95 1.21 s; 0 errors | Performance pass; quality fail at 70% |
| Q8 reasoning, concurrency 16 | 459.7 output tok/s; 30.0 tok/s/GB; p95 1.71 s; 0 errors | Slower with no measured quality gain |
| STT, concurrency 4 | 2.01 requests/s; p95 1.96 s; 35.5x audio real time | Last point inside proposed STT p95 gate |
| TTS, concurrency 1 | 2.19 requests/s; full-WAV p95 0.48 s; latency RTF 0.059 | TTFA not directly measured |
| Combined turn, concurrency 4 | 37.3 turns/min; p95 6.95 s; 0 errors | Highest error-free point, but p50 and STT SLOs fail |
| Combined turn, concurrency 8 | 34.4 successful turns/min; 20% HTTP 504 | STT queue crossed the 10-second deadline |

vLLM's Prometheus and per-request metrics are the source for queue time, TTFT, and TPOT when the
installed release exposes them. The legacy reasoning artifacts use server TTFT when available and
client TTFT only as a fallback; they do not retain both separately. Combined `ttft_ms` is the
non-streaming reasoning adapter's first-result time, not token TTFT. The fixed
20-conversation corpus provides a fast local curve. The selected `.env.example` admission limit is
four model calls as provisional overload protection (settings alone default to eight), while
`compose.load-test.yaml` admits 16
only for saturation tests. This is not a validated full-interview concurrency limit. A resume-safe
maximum-stable-concurrency claim requires a sustained confirmation run with enough samples for
tail-latency reporting.

## Initial production SLO proposal

These targets are realistic starting gates for a turn-based interview, not current achievements:

| Objective | Proposed target at admitted load |
|---|---:|
| WebSocket/session establishment | p95 <= 2 s; success >= 99.5% |
| STT finalization after commit | p95 <= 2 s; timeout < 0.5% |
| Reasoning queue time | p95 <= 500 ms; oldest queue age < 2 s |
| Reasoning analysis | p95 <= 4 s; schema-valid >= 99.5% after bounded retry |
| TTS first audio | p95 <= 800 ms warm; synthesis RTF <= 0.2 |
| Speech-end to next-audio | p50 <= 3 s; p95 <= 7 s |
| Turn endpoint correctness | false cut < 2%; manual commit < 5% |
| Availability | 99.9% API/session tier; independent text fallbacks for speech failure |
| Overload | reject/degrade before p95 exceeds deadline; never grow an unbounded queue |

Quality gates accompany every performance gate: turn-disposition F1 by class, gibberish and
do-not-know false-positive rates, follow-up groundedness, transcript word error rate across the
approved evaluation cohorts, and reviewer agreement. Voice/accent/emotion are never scoring inputs.

## Production self-hosted sizing estimates

### Recommended first production shape

Start with independently scalable pools:

| Pool | Initial deployment | Planning capacity | Why |
|---|---|---:|---|
| API/WebSocket | 2 CPU replicas behind a load balancer | Thousands of mostly idle sockets after load test | Connections are lightweight; audio buffers must remain bounded |
| STT | At least 2 GPU workers, batched by Speaches/faster-whisper | Start from 4 admitted commits per measured worker | One local worker plateaued near 2 requests/s; validate accuracy and burst tails |
| Reasoning | At least 2 vLLM replicas on validated L4/L40S/H100-class GPUs | Q4 sensitivity scenarios: ~320/~930/~2,280 output tok/s per GPU | Heuristic projections from measured 48.7 tok/s/GB and bandwidth; not guaranteed budgets |
| TTS | At least 2 independently admitted Kokoro workers | Start from 1 latency-sensitive synthesis per measured worker | Local full-WAV p95 crossed 800 ms at concurrency 2; streaming TTFA must be measured separately |
| State/queue | HA PostgreSQL plus durable queue/outbox | Sized from admitted sessions and retention | Prevent duplicate turns and preserve recovery/audit state |

Do not infer replica capacity from parameter count alone. First measure the exact model,
quantization, workload, engine, context ceiling, and batching policy; then use normalized
tok/s/GiB only as one cross-hardware input. Memory bandwidth, kernel support, KV-cache headroom,
tail latency, and the interview quality suite remain independent gates.

The production-GPU figures above are illustrative estimates from the smaller of the report's
VRAM-normalized and memory-bandwidth-scaled projections, followed by a 65% headroom factor. Neither
projection is a proven bound. These are reasoning-only scenarios, not end-to-end interview throughput.

At 12 analyzed turns per 45-minute interview, 20 simultaneous interviews create an average of only
about **5.3 reasoning requests/minute** (`20 * 12 / 45`), but arrivals are bursty and intake planning
requests are longer. Voice sessions therefore tend to be latency-bound before they are aggregate
token-throughput-bound. Size with replayed arrival bursts, not a uniform average.

### Capacity formulas

```text
STT worker concurrency ~= floor(target_utilization / RTF)
reasoning requests/min  = concurrent_interviews * analyzed_turns / interview_minutes
required reasoning tok/s = requests/s * mean_generated_tokens / target_utilization
TTS required audio rate = interviews * interviewer_audio_seconds / wall_seconds
replicas = ceil(peak_required_capacity / tested_capacity_per_replica) + failure headroom
```

Use `target_utilization <= 0.65` for the first latency-sensitive deployment and require N+1 capacity.
Do not turn the formulas into an SLA until replay tests show the same prompt lengths, output lengths,
speech/noise mix, and retry rates as production.

## Measurement plan

1. Create a synthetic replay corpus with short, normal, long, silent, gibberish, profanity,
   clarification, repeat, do-not-know, and technical-failure turns.
2. Warm each model, then measure cold start separately.
3. Run concurrency `1, 2, 4, 8, ...` independently for STT, reasoning, and TTS. Hold prompt/audio
   distributions constant and stop when the proposed p95 or error gate fails.
4. Run the complete WebSocket interview path with realistic think time and bursty turn endings.
5. Record p50/p95/p99, successful throughput, utilization, queue age, schema validity, transcript
   quality, and candidate-visible transition time for every point.
6. Kill one provider replica during load and prove typed/text degradation, idempotency, bounded queue
   growth, and recovery.
7. Recalculate cost per completed interview from measured utilization and compare it with the hosted
   model worksheet in `INFERENCE_DEPLOYMENT_ANALYSIS.md`.

Only the measured report should replace the estimates in this document. Preserve the assumptions so
future hardware or model changes remain comparable.
