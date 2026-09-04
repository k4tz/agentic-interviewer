# Standard vLLM load-testing procedure

## Authority and scope

This runbook implements *GenAI Agentic Load Testing Standard 1.0*. It supersedes the deleted
llama.cpp/Qwen snapshots and is the required procedure for new capacity claims. Results are
hardware-and-workload observations, not general claims about a model family.

The full standard includes the same-family Q4/Q8 comparison below. A user-scoped campaign may omit
additional Q8 runs; record that deviation instead of calling it full-standard compliance. Existing
Q8 measurements were left unchanged in the retained campaign. These commands document future
explicitly requested runs; they do not imply any benchmark should automatically restart.

The two reasoning variants deliberately share one base model and serving engine:

| Variant | Hugging Face model | Pinned revision | Served name | Purpose |
|---|---|---|---|---|
| Q4 | `RedHatAI/Mistral-7B-Instruct-v0.3-quantized.w4a16` | `aff481973dbd56b97c6538e06556fb5e3dab939e` | `mistral-7b-instruct-v0.3-q4` | Higher-capacity point |
| Q8 | `RedHatAI/Mistral-7B-Instruct-v0.3-quantized.w8a16` | `5079e6662632d88ac726516beccf202c925402a0` | `mistral-7b-instruct-v0.3-q8` | Same-family quality/capacity comparison |

Both profiles run through the official `vllm/vllm-openai:v0.26.0` image with continuous batching,
prefix caching, per-request metrics, a fixed 8,192-token context ceiling, and 16 maximum sequences.
Only one profile may bind port 8080 at a time.

Both variants use the pinned Q4 repository's tokenizer/chat template. The Q8 repository ships a
different template that rejects an initial system message, while the application reasoning
contract uses system context. Sharing the same base-model tokenizer keeps request formatting fixed
across the quantization comparison; changing templates would confound both quality and throughput.

This Windows Docker Desktop/WSL2 host sets `VLLM_USE_V2_MODEL_RUNNER=0`. vLLM 0.26's V2 model
runner requires CUDA unified virtual addressing (UVA), which is unavailable through this host's
WSL2 GPU path and otherwise fails before model loading. The V1 runner preserves vLLM request
scheduling and continuous batching; record this environment difference when comparing native-Linux
public results.

## Required disclosure

Every result directory must identify the run timestamp, OS/WSL/Docker versions, CPU and RAM, GPU,
driver, available VRAM before model start, vLLM image, exact model revision when available,
quantization, context length, `max-num-seqs`, GPU-memory-utilization, workload revision, request
count, warm-up count, and concurrency ladder. Record deviations beside the result; never silently
compare incompatible runs.

The fixed reasoning corpus is
[`evaluation/fixtures/reasoning_conversations_v1.json`](../evaluation/fixtures/reasoning_conversations_v1.json).
It contains 20 labeled interview turns. The audio fixture is
`evaluation/fixtures/reference_interview_answer.wav`. Do not change fixtures between Q4 and Q8.

## Phase 0 - clean baseline and environment

Stop application services and both reasoning profiles. Model caches are persistent inputs and are
not benchmark results; do not delete them between equivalent runs.

```powershell
docker compose down
docker compose -f compose.vllm.yaml --profile q4 --profile q8 down
docker ps --format "table {{.Names}}\t{{.Status}}"
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free --format=csv
docker version
```

Create one immutable result directory such as `evaluation/results/2026-09-03-vllm-standard-v1/`.
Do not mix retries or model variants into a prior directory.

## Phase 1 and 2 - isolated services

Discard five warm-up requests before every recorded profile. First run concurrency 1, then the
same fixed workload at `1 2 4 8 16`. Keep only the service under test loaded on the GPU.

### Reasoning Q4

```powershell
$env:VLLM_Q4_GPU_MEMORY_UTILIZATION = "0.90"
docker compose -f compose.vllm.yaml --profile q4 up -d vllm-q4
docker compose -f compose.vllm.yaml logs -f vllm-q4

uv run python scripts/standard_benchmark.py --profile reasoning --phase isolated-baseline `
  --base-url http://127.0.0.1:8080 --model mistral-7b-instruct-v0.3-q4 `
  --quantization GPTQ-W4A16 --concurrency 1 --requests 20 --warmup-requests 5 `
  --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/reasoning-q4-baseline.json

uv run python scripts/standard_benchmark.py --profile reasoning --phase isolated-sweep `
  --base-url http://127.0.0.1:8080 --model mistral-7b-instruct-v0.3-q4 `
  --quantization GPTQ-W4A16 --concurrency 1 2 4 8 16 --requests 20 `
  --warmup-requests 5 --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/reasoning-q4-sweep.json
```

Stop Q4 before Q8. Start service `vllm-q8`, use served model
`mistral-7b-instruct-v0.3-q8` and quantization `GPTQ-W8A16`, and repeat both commands into
`reasoning-q8-baseline.json` and `reasoning-q8-sweep.json` with a new pre-start baseline.

### Speech

Stop vLLM, then start Speaches alone and load the fixed models. Restart Speaches between the STT
and TTS profiles so the other speech model is not resident during an isolated measurement.

```powershell
docker compose up -d speaches
Invoke-RestMethod -Method Post http://127.0.0.1:8001/v1/models/Systran/faster-distil-whisper-small.en
Invoke-RestMethod -Method Post http://127.0.0.1:8001/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX

uv run python scripts/standard_benchmark.py --profile stt --phase isolated-baseline `
  --base-url http://127.0.0.1:8001 --model Systran/faster-distil-whisper-small.en `
  --concurrency 1 --requests 20 --warmup-requests 5 `
  --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/stt-baseline.json

uv run python scripts/standard_benchmark.py --profile stt --phase isolated-sweep `
  --base-url http://127.0.0.1:8001 --model Systran/faster-distil-whisper-small.en `
  --concurrency 1 2 4 8 16 --requests 20 --warmup-requests 5 `
  --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/stt-sweep.json

docker compose restart speaches

uv run python scripts/standard_benchmark.py --profile tts --phase isolated-baseline `
  --base-url http://127.0.0.1:8001 --model speaches-ai/Kokoro-82M-v1.0-ONNX `
  --concurrency 1 --requests 20 --warmup-requests 5 `
  --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/tts-baseline.json

uv run python scripts/standard_benchmark.py --profile tts --phase isolated-sweep `
  --base-url http://127.0.0.1:8001 --model speaches-ai/Kokoro-82M-v1.0-ONNX `
  --concurrency 1 2 4 8 16 --requests 20 --warmup-requests 5 `
  --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/tts-sweep.json
```

## Phase 3 - combined candidate-turn path

Load Q4, Speaches, PostgreSQL, and the app together. The benchmark creates synthetic sessions
before the timed window, then measures one audio answer through STT, answer reasoning, application
routing, and next-question TTS. Session preparation is intentionally excluded from turn latency.

```powershell
$env:VLLM_Q4_GPU_MEMORY_UTILIZATION = "0.50"
docker compose -f compose.vllm.yaml --profile q4 up -d vllm-q4
docker compose up -d postgres speaches
docker compose -f compose.yaml -f compose.load-test.yaml up -d --build app

uv run python scripts/standard_benchmark.py --profile pipeline --phase combined `
  --base-url http://127.0.0.1:8000 --model mistral-7b-instruct-v0.3-q4 `
  --quantization GPTQ-W4A16 --concurrency 1 2 4 8 16 --requests 20 `
  --gpu-memory-utilization 0.50 `
  --warmup-requests 5 --baseline-vram-mib <PHASE_0_USED_MIB> `
  --output evaluation/results/<RUN_ID>/pipeline-sweep.json
```

Run the combined phase only after every isolated result is complete. This avoids contaminating
single-service VRAM and throughput figures. The combined profile deliberately lowers vLLM's GPU
reservation to 0.50 so Speaches has explicit headroom on a 16 GiB card; this is a separate capacity
profile and must not be presented as the isolated 0.90 result.

`compose.load-test.yaml` raises the application request bucket and model-call admission limit only
for the benchmark. It prevents normal control-plane throttling from hiding the provider saturation
boundary. After the sweep, recreate the app from the base file so normal runtime safeguards return:

```powershell
docker compose up -d --force-recreate app
```

## Metrics and gates

The harness records p50/p95/p99/max E2E latency, reasoning TTFT/TPOT/queue time when exposed,
application orchestration overhead, RPS, completed requests/minute, output tokens/second,
output-tokens/second/decimal-GB (and GiB) of measured VRAM above the pre-start baseline, STT/TTS
latency RTF, task-success rate, status counts, failures, and raw samples. Legacy `sessions_per_minute`
means requests/turns per minute, not whole interviews. Legacy `aggregate_rtf` means summed
request-stage latency / summed audio duration, not wall time / summed audio duration.

The fixed quality check is part of the load result: a reasoning response succeeds only when it is
valid structured output and its disposition matches the labeled turn. A fast but incorrect or
malformed response cannot establish stable capacity.

For this local calibration, 20 measured requests per level expose the curve quickly. A publishable
maximum-stable-concurrency claim additionally requires a sustained run of at least 10 minutes and
enough samples for meaningful p99 reporting. The stable point is the highest level before latency,
quality, timeout/error, queue-growth, or OOM gates fail; it is not automatically the level with the
largest raw throughput.

## Phase 4 - normalization and extrapolation

Normalize each measured reasoning point as:

```text
output tok/s/GB VRAM = aggregate successful output tokens / wall seconds
                       / peak allocated decimal GB above the pre-start baseline
```

The JSON also retains the binary-GiB form to remove unit ambiguity. Keep raw tok/s beside the
normalized value. Extrapolate to another GPU only after considering
memory fit, memory bandwidth, compute capability, quantization-kernel support, KV-cache headroom,
and the same latency/quality gates. Cross-check estimates against public results using a comparable
model, precision, context, prompt/output distribution, batching policy, and serving engine.

## Result integrity

- Never average Q4 and Q8 into one capacity number.
- Never treat a failed or timed-out request as throughput.
- Preserve raw JSON and commands; summarize into a separate report.
- Label cold-start, warm, isolated, and combined numbers independently.
- Do not reuse the deleted llama.cpp/Qwen reports as current evidence.

The completed short-run report is
[`LOAD_TEST_REPORT_VLLM_2026-09-03.md`](reports/LOAD_TEST_REPORT_VLLM_2026-09-03.md). That campaign's
generated JSON was removed at the user's request on 2026-09-04; the report is now its retained
historical summary. For new campaigns, retain raw JSON until cleanup is requested and validate it
without invoking models:

```powershell
uv run python scripts/validate_standard_results.py evaluation/results/<RUN_ID> `
  --output evaluation/results/<RUN_ID>/validation.json
uv run python -m pytest -o addopts='' -q
uv run ruff check .
```

An integrity pass confirms consistent saved arithmetic and fixtures, not a performance/quality
gate pass. Reusing a different currently running model requires a new campaign directory.
