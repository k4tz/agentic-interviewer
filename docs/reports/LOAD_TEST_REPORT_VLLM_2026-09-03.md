# Standard vLLM load-test report - 2026-09-03

## Result status

This campaign is complete for the short calibration defined by *GenAI Agentic Load Testing
Standard 1.0*. The source procedure is
`F:\Projects\GenAI_Agentic_Load_Testing_Standard.docx`. It replaced the retired llama.cpp/Qwen
snapshots with isolated and combined measurements from a pinned vLLM/Speaches stack.

The existing Q8 measurements were retained without another sweep. These tables were derived from
the final JSON files formerly in `evaluation/results/2026-09-03-vllm-standard-v1/`.

### Retention note - 2026-09-04

At the user's request, this latest report was moved to `docs/reports/`, and all generated test
results, environment metadata, and validation JSON were removed. The test code, benchmark/audit
scripts, fixtures, and runbook remain available for new runs. This report preserves the historical
findings; the deleted per-request evidence can no longer be re-audited without a fresh run.

## Executive outcome

- Q4 reasoning reached **741.9 output tok/s** at concurrency 16, or **48.7 tok/s per decimal GB
  of allocated VRAM**, with 0/100 request failures across the ladder.
- Q8 reasoning reached **459.7 output tok/s** and **30.0 tok/s/GB** at concurrency 16, also with
  0/100 request failures. Q4 delivered 61% more aggregate output throughput at that point.
- Both reasoning profiles produced only **90% schema-valid** and **70% disposition-correct**
  outputs on the fixed 20-case interview corpus. They pass the transport/performance exercise but
  fail the quality admission gate; neither is a production-approved interviewer model yet.
- Isolated Whisper STT saturated near **2.0 requests/s** (about 35 audio-seconds processed per
  wall-second). Its p95 remained under the proposed 2-second STT gate through concurrency 4.
- Isolated Kokoro TTS peaked at **2.85 requests/s** at concurrency 8, but p95 had risen to 3.02
  seconds. At concurrency 16, VRAM reached 16,010 MiB and throughput collapsed to 0.62 requests/s.
- The full persisted candidate-turn path was error-free through concurrency 4. At that point it
  completed **37.3 turns/minute**, with p50 **6.46 seconds** and p95 **6.95 seconds**. This is the
  highest error-free tested point, but its median fails the proposed 3-second target.
- At concurrency 8 and 16, combined success fell to 80% and 40%. Failures were HTTP 504
  `provider_timeout` responses after the one-worker STT queue crossed the application's 10-second
  deadline. Peak combined VRAM was 13,832 MiB (13.51 GiB), so this was queue/deadline saturation,
  not an observed OOM.

These are warm, local, workload-specific measurements. A resume-safe sustained-capacity claim still
requires the standard's 10-minute confirmation run and a larger sample for meaningful p99 values.

## Test contract

| Item | Fixed value |
|---|---|
| Host | Windows 10, Docker Desktop/WSL2, i5-12400F, 15.82 GiB RAM |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16,311 MiB, driver 591.74 |
| Reasoning engine | `vllm/vllm-openai:v0.26.0`, V1 runner on WSL2 |
| Reasoning variants | Pinned Mistral-7B-Instruct-v0.3 GPTQ W4A16 and W8A16 |
| Isolated vLLM reservation | 0.90 |
| Combined vLLM reservation | 0.50, leaving headroom for GPU STT/TTS |
| Speech | Speaches CUDA 12.6.3; faster-distil-whisper-small.en FP16; Kokoro-82M ONNX CUDA-first |
| Ladder | 1, 2, 4, 8, 16 concurrent requests |
| Volume | 20 recorded requests per level; 5 warm-ups discarded per profile |
| Reasoning fixture | 20 labeled answer-analysis turns; SHA-256 `b760197a...f416624` |
| Audio fixture | 17.664-second mono WAV; SHA-256 `ce9a5cf2...089472b` |

The harness created interview sessions before each timed combined window. A timed candidate turn
then traversed binary audio upload, STT, persisted answer handling, LLM next-action selection, TTS,
and state retrieval. The load-test Compose override admitted all 16 clients and raised only the
control-plane request bucket so application throttling could not conceal provider saturation.

## Isolated reasoning

### Q4 - GPTQ W4A16

| Concurrency | Success | Output tok/s | Tok/s/GB VRAM | E2E p95 ms | TTFT p95 ms | TPOT p95 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20/20 | 85.0 | 5.58 | 1,001.7 | 27.6 | 11.5 |
| 2 | 20/20 | 154.7 | 10.15 | 1,116.1 | 192.1 | 11.7 |
| 4 | 20/20 | 287.0 | 18.83 | 1,046.7 | 26.9 | 14.6 |
| 8 | 20/20 | 513.7 | 33.71 | 1,082.0 | 43.7 | 12.5 |
| 16 | 20/20 | **741.9** | **48.68** | 1,207.1 | 73.4 | 13.2 |

The concurrency-2 TTFT tail is a repeatable outlier in this short sample, not a monotonic trend.
Queue p95 remained below one millisecond at every Q4 level, so vLLM continuous batching still had
headroom for this short-output workload at concurrency 16.

### Q8 - GPTQ W8A16 (retained result)

| Concurrency | Success | Output tok/s | Tok/s/GB VRAM | E2E p95 ms | TTFT p95 ms | TPOT p95 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20/20 | 50.5 | 3.30 | 1,620.9 | 22.6 | 19.5 |
| 2 | 20/20 | 93.8 | 6.13 | 1,654.6 | 188.6 | 19.8 |
| 4 | 20/20 | 172.7 | 11.29 | 1,670.8 | 41.0 | 22.6 |
| 8 | 20/20 | 318.9 | 20.84 | 1,683.1 | 57.0 | 20.4 |
| 16 | 20/20 | **459.7** | **30.05** | 1,706.6 | 80.0 | 21.2 |

### Quality gate

At every reasoning level, 18/20 responses parsed into the required schema and 14/20 matched the
expected disposition. The consistently correct cases were 12 `accept` cases and two explicit
`skip` cases. Failures concentrated in `follow_up`, `insufficient`, and `gibberish`, including two
malformed outputs. Q8 did not improve this score over Q4, so its lower throughput bought no measured
quality improvement on this corpus.

This quality failure is more important than the raw concurrency ceiling. The next model/prompt
iteration should target class-balanced disposition accuracy and structured-output reliability
before a longer capacity certification.

## Isolated speech

### STT - faster-distil-whisper-small.en, one FP16 CUDA worker

| Concurrency | Success | Requests/s | Audio x real time | Latency RTF | E2E p95 ms | Peak GPU MiB |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20/20 | 1.70 | 30.10x | 0.033 | 635.8 | 1,538 |
| 2 | 20/20 | 2.00 | 35.29x | 0.055 | 1,005.7 | 1,542 |
| 4 | 20/20 | **2.01** | **35.48x** | 0.106 | 1,963.7 | 1,542 |
| 8 | 20/20 | 1.91 | 33.77x | 0.203 | 4,534.7 | 1,542 |
| 16 | 20/20 | 1.87 | 33.10x | 0.343 | 8,930.3 | 1,547 |

Throughput plateaus at concurrency 2-4 while per-request latency grows almost linearly afterward.
The provider keeps returning nonempty transcripts but is latency/queue-bound with one worker.
STT success here checks only for at least five transcribed words, not word-error rate; TTS success
checks for a decodable, nonempty WAV, not pronunciation or listening quality.

### TTS - Kokoro-82M ONNX, CUDA-first

| Concurrency | Success | Requests/s | Audio x real time | Latency RTF | E2E p95 ms | Peak GPU MiB |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20/20 | 2.19 | 16.83x | 0.059 | 479.1 | 3,095 |
| 2 | 20/20 | 2.52 | 19.37x | 0.100 | 876.1 | 5,275 |
| 4 | 20/20 | 2.79 | 21.43x | 0.178 | 1,654.1 | 7,587 |
| 8 | 20/20 | **2.85** | **21.86x** | 0.325 | 3,018.9 | 14,255 |
| 16 | 20/20 | 0.62 | 4.75x | 2.187 | 26,374.6 | 16,010 |

The throughput knee is concurrency 4-8. Concurrency 16 is a pressure point: it consumes nearly all
VRAM and request latency exceeds generated audio duration, while aggregate throughput still
produces 4.75 audio seconds per wall-second. These measurements cover complete WAV generation,
not streaming time-to-first-audio. The legacy JSON keys `stt_aggregate_rtf` and `tts_aggregate_rtf`
mean sum of successful request-stage latency divided by sum of audio duration. With concurrency,
this latency ratio is not the inverse of aggregate audio-throughput; queueing is included.

## Combined candidate-turn path

| Concurrency | Success | Error rate | Turns/s | Turns/min | E2E p50 ms | E2E p95 ms | STT p95 ms | Reasoning p95 ms | TTS p95 ms | Peak GPU MiB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20/20 | 0% | 0.538 | 32.3 | 1,845.9 | 1,938.8 | 617 | 321 | 946 | 11,651 |
| 2 | 20/20 | 0% | 0.594 | 35.6 | 3,360.0 | 3,640.5 | 1,625 | 773 | 1,441 | 13,829 |
| 4 | 20/20 | 0% | **0.621** | **37.3** | 6,456.2 | **6,948.9** | 4,844 | 763 | 1,425 | 13,827 |
| 8 | 16/20 | 20% | 0.573 | 34.4 | 9,500.2 | 11,341.7 | 9,453 | 796 | 1,523 | 13,832 |
| 16 | 8/20 | 60% | 0.426 | 25.6 | 7,462.0* | 11,603.6* | 9,601 | 795 | 1,861 | 13,832 |

`*` Latency percentiles include successful samples only; the failed requests terminated around the
10-second provider deadline. They must not be interpreted as an improvement at concurrency 16.

Concurrency 4 is the last zero-error level, not an SLO-qualified stable capacity. Its p95 is below
7 seconds, but p50 exceeds 3 seconds and STT p95 exceeds 2 seconds. Only concurrency 1 meets both
proposed overall p50/p95 limits in this HTTP path; actual speech-end-to-first-audio, WebSocket/VAD
behavior, and first-audio TTS latency were not measured. Reasoning quality also fails independently.
No production-admitted concurrency is established by this campaign.

The configured four-model-call process/tenant limit is provisional development overload protection,
not a guarantee of four simultaneous interviews or four full candidate turns. `compose.load-test.yaml`
raises admission to 16 only while finding the saturation boundary.

## Cross-hardware reasoning estimate

The following is an estimate, not a benchmark. It uses the measured Q4 concurrency-16 value of
741.9 tok/s and 48.68 tok/s/GB, then evaluates two heuristic projections:

```text
VRAM projection      = 48.68 measured tok/s/GB * 90% of target VRAM
bandwidth projection = 741.9 measured tok/s * target bandwidth / 448 GB/s local bandwidth
planning scenario    = minimum(VRAM projection, bandwidth projection)
headroom scenario    = 65% of the planning scenario
```

The local board's 448 GB/s figure comes from the board-vendor specification. NVIDIA specifies
24 GB and 300 GB/s for L4, 48 GB and 864 GB/s for L40S, and 80 GB with 3.35 TB/s for H100 SXM.

| Target reasoning GPU | VRAM | Bandwidth | VRAM projection | Bandwidth projection | Planning scenario | 65% scenario |
|---|---:|---:|---:|---:|---:|---:|
| NVIDIA L4 | 24 GB | 300 GB/s | 1,052 tok/s | 497 tok/s | **~500 tok/s** | **~320 tok/s** |
| NVIDIA L40S | 48 GB | 864 GB/s | 2,103 tok/s | 1,431 tok/s | **~1,430 tok/s** | **~930 tok/s** |
| NVIDIA H100 SXM | 80 GB | 3,350 GB/s | 3,505 tok/s | 5,548 tok/s | **~3,500 tok/s** | **~2,280 tok/s** |

Sources: [vLLM Docker deployment](https://docs.vllm.ai/en/latest/deployment/docker/),
[vLLM production metrics](https://docs.vllm.ai/en/latest/design/metrics/),
[RTX 5060 Ti board specification](https://www.pny.com/File%20Library/Company/Support/Product%20Brochures/GeForce%20Graphics/English/rtx-5060-ti-16gb-dual-fan-brochure.pdf),
[NVIDIA L4 specification](https://images.nvidia.com/aem-dam/Solutions/geforce/ada/nvidia-ada-gpu-architecture.pdf),
[NVIDIA L40S specification](https://www.nvidia.com/es-la/data-center/l40s/), and
[NVIDIA H100 specification](https://www.nvidia.com/en-us/data-center/h100/).

These are sensitivity scenarios, not mathematical upper bounds, guaranteed conservative budgets,
or measurements on those GPUs. A matching public benchmark for this exact model/kernel/workload
was not established. Target architecture,
clocking, memory hierarchy, Marlin/AutoGPTQ kernel support, native-Linux runner behavior, prompt
length, batch shape, and KV-cache occupancy can move real throughput substantially. Validate each
target with the same fixture and report both raw tok/s and tok/s/GB. Speech should run in an
independently scalable pool; otherwise the current one-worker STT ceiling dominates regardless of
reasoning-GPU speed.

## Reproducibility and deviations

- vLLM ran its V1 runner because Docker Desktop/WSL2 did not expose the UVA capability required by
  vLLM 0.26's V2 runner. Native Linux should be remeasured rather than assigned these exact tails.
- Q4 and Q8 used the same pinned system-message-capable tokenizer template to prevent request-format
  drift. Q8 was not rerun after the user asked to preserve its completed artifact.
- Five warm-ups were excluded, but each level has only 20 measured requests. p99 equals or closely
  tracks a single tail sample and is diagnostic, not statistically strong.
- GPU use was sampled about every 250 ms, so very short transient peaks may be missed.
- The combined profile measures committed HTTP candidate turns. It excludes candidate think time,
  browser capture, VAD endpoint delay, network transit, and session preparation.
- Combined task success proves STT/reasoning/TTS completion and persisted state. General answer
  judgment quality is represented only by the isolated labeled reasoning corpus.
- Legacy speech-only JSON `engine_image`, `engine_runner`, and vLLM-related fields inherited the
  harness's reasoning defaults. They do not describe the STT/TTS engine; the actual speech settings
  were recorded in the now-removed `environment.json` and are summarized in the test contract above.
- Combined `ttft_ms` means non-streaming reasoning time-to-first-result, not token TTFT. The isolated
  artifacts prefer server TTFT and do not separately retain client TTFT when server timing exists.
- Cold initial model acquisition was observed separately (about 240 seconds for Q4 weights and 338
  seconds for Q8 weights). Warm readiness depends on compile-cache validity and reservation shape.

## Production decisions

1. The measured single-GPU development profile used a 0.50 Q4 reservation. Its provisional
   admission limit is 4 model calls, not a production-certified interview concurrency.
2. Split STT, reasoning, and TTS into separately autoscaled GPU pools. Add STT workers first; it is
   the measured combined bottleneck.
3. Improve the reasoning disposition prompt/model and reach the quality threshold before doing the
   10-minute stable-capacity confirmation.
4. Add streaming TTS/TTFA instrumentation and replay WebSocket/VAD traffic so the candidate-visible
   transition metric covers the full voice path.
5. For any target GPU, repeat the exact pinned test before turning the extrapolation table into a
   cost or SLA claim.

## Resume-safe evidence statements

- Built a reproducible vLLM/Speaches load harness with fixed fixtures, discarded warm-ups,
  per-stage latency, error, quality, GPU, and tok/s/GB reporting across 1-16 concurrent requests.
- Measured 741.9 aggregate output tok/s (48.7 tok/s/GB allocated VRAM) for a pinned 7B GPTQ Q4 model
  on a 16 GB RTX 5060 Ti, with zero reasoning transport errors across 100 measured requests.
- Identified a one-worker STT deadline bottleneck through end-to-end testing: the full candidate-turn
  path was error-free through concurrency 4 and reached 37.3 completed turns/minute in the short run.
- Enforced a quality gate that rejected both Q4 and Q8 configurations at 70% disposition accuracy,
  preventing throughput-only results from being presented as production readiness.

Use “short calibration” with these numbers until the sustained confirmation is run.

## Final validation - 2026-09-04

- All ten raw result files passed arithmetic, sample-count, fixture-hash, and ladder validation:
  30 measured points, 600 recorded requests, 584 HTTP successes and 16 failures. All failures are in
  the combined concurrency-8/16 points. Five warm-ups per invocation are outside these counts.
- Offline suite: **174 passed**, with two existing upstream deprecation warnings; Ruff: clean.
- Both the application/load-test overlay and Q4/Q8 Compose configurations validate without starting
  services. The benchmark parser now rejects non-string disposition values without crashing.
- Q8 was neither rerun nor edited during final validation. Its original SHA-256 values and every
  other raw artifact digest were verified in `validation-2026-09-04.json`, subsequently removed
  during the requested artifact cleanup.
- A separate `vllm-llama-3b` container was found running on port 8080 during final validation, serving
  `casperhansen/llama-3.2-3b-instruct-awq` as `llama-3b`. It was left untouched and is not represented
  by these Mistral measurements. The app, PostgreSQL and Speaches containers were stopped; no claim
  is made that the measured full stack is currently running.
- The short calibration and regression/audit work are complete. A full voice-path, quality-admitted,
  sustained production certification is **not** complete and cannot be inferred from these results.
