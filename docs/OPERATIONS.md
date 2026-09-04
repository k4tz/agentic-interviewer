# Operations and evaluation

The assets in this repository are a production-oriented baseline, not evidence that a production
deployment or drill has passed. Application audit events remain distinct from operational spans
and metrics. Candidate answers, resume contents, transcripts, email addresses, and raw audio must
never be emitted as telemetry.

## Controls and ownership

| Control | Primitive | Expected integration point | Fail-closed outcome |
|---|---|---|---|
| Per-tenant request rate | `RateLimiter` | API edge before request parsing beyond IDs | HTTP 429 with bounded retry hint |
| Global and tenant concurrency | `AdmissionController` | Before starting model/media work | Reject/reschedule; do not start degraded voice |
| Provider failure isolation | `CircuitBreaker` | Around admitted provider calls | Open after threshold; allow one recovery probe |
| Priority/depth | `PriorityWorkQueue` | Worker dispatch reference | Reject when bounded depth is full |
| Session tokens/audio/TTS/cost | `SessionBudget` | Immediately after each normalized provider usage result | Pause or safe close; authorized override is separate |
| Request deadline/queue/output | Existing `ExecutionControls` | Capability router and every adapter call | Reject a route that cannot honor the controls |

The in-memory implementations are deterministic reference primitives. A multi-replica deployment
must provide equivalent atomic semantics with a shared store or admission service. Never treat a
per-process limiter as a tenant-wide production quota.

## Speech-provider operating contract

Speaches supplies both STT and TTS on the GPU in the selected local profile; vLLM serves the
reasoning model. Dedicated Speaches adapters own its model/voice defaults and upstream protocol;
generic OpenAI-compatible audio adapters can target an admitted compatible endpoint. Concrete
selection lives in `composition/providers.py`, not the API relay or workflow.
Treat a base-URL change as a provider change: rerun wire,
capability, privacy, retention, region, fidelity, latency, and failure-mode conformance before
admission.

The current HTTP adapter is buffered and must be monitored for upload-to-final-transcript and
request-to-complete-audio latency. The separate realtime transcription port/adapter needs
input-audio append rate, buffer duration, server-VAD/manual-commit events,
commit-to-final-transcript latency, empty/failed turns, and correction rate. Incremental audio
ingress does not imply dependable partial transcript events; downstream reasoning and scoring must
wait for the completed current-turn transcript. For current buffered TTS, monitor full-response
latency, completion/failure, and synthesized characters. Time to first playable audio and stream
gaps require a future admitted streaming implementation. Interruption/barge-in readiness
is not an MVP gate; turn-based playback and explicit stop/repeat controls are acceptable.

## Operate the candidate browser interview

Start PostgreSQL, Speaches, and the selected vLLM profile first. Ensure the configured model IDs are
available, then use the local profile:

```text
DATABASE_URL=postgresql://interviewer:interviewer@127.0.0.1:5433/interviewer
PROVIDER_PROFILE=local-specialized
SPEACHES_BASE_URL=http://127.0.0.1:8001
SPEACHES_STT_MODEL=Systran/faster-distil-whisper-small.en
SPEACHES_TTS_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX
REASONING_PROVIDER_NAME=vllm
REASONING_BASE_URL=http://127.0.0.1:8080
REASONING_MODEL=mistral-7b-instruct-v0.3-q4
REASONING_REGION=local
REASONING_EXTERNAL_PROCESSING=false
REASONING_RETAINS_PROVIDER_DATA=false
REASONING_CHECK_HEALTH_ENDPOINT=true
VLLM_Q4_GPU_MEMORY_UTILIZATION=0.50
REASONING_MAX_COMPLETION_TOKENS=1024
REASONING_TEMPERATURE=0.3
REASONING_TOP_P=0.9
REASONING_PRESENCE_PENALTY=0.0
MAX_CONCURRENT_MODEL_CALLS=4
MAX_CONCURRENT_MODEL_CALLS_PER_TENANT=4
CANDIDATE_UI_ENABLED=true
REALTIME_MAX_MESSAGE_BYTES=1048576
REALTIME_CONNECT_TIMEOUT_SECONDS=10
```

Live generic endpoints/models must be explicit. Generic synthesis also requires `AUDIO_DEFAULT_VOICE`
or a request voice; Speaches uses its adapter-owned `SPEACHES_DEFAULT_VOICE` default. Generic
reasoning/audio privacy metadata defaults conservatively to external processing with retention;
the local values above are explicit deployment declarations. See the guide's configuration section
before connecting a hosted endpoint. A local URL does not prove local processing.

Run the Q4 service from `compose.vllm.yaml`, run `docker compose up -d postgres speaches`, download
the configured speech models once, then run
`docker compose up -d --build app` and open `http://127.0.0.1:8000/`. The
`speaches-model-cache` volume survives container replacement. The CUDA profile sets Whisper to
`float16`, selects CUDA-first ONNX Runtime for Kokoro, and retains both loaded models after warm-up.
Upload only
synthetic data until production data controls are approved. Confirm a job loads, prepare the intake,
grant microphone permission, begin, listen to one TTS question, confirm that capture starts
automatically, speak one answer, and confirm that VAD advances to the next question. The existing
`GET /metrics` endpoint remains
application-wide and must not acquire resume text, transcripts, raw audio, or high-cardinality
labels.

The selected Speaches session schema requires `session.turn_detection.prefix_padding_ms` while also
reporting it as non-configurable. `adapters/speaches_realtime.py` supplies the provider's existing value,
suppresses only that known setup warning, disables provider-generated responses, and waits for
the provider acknowledgement before emitting an application-owned ready event. After TTS ends, the browser starts
24 kHz PCM16 capture automatically and server VAD commits the answer after
`REALTIME_SILENCE_DURATION_MS`. **Done speaking** remains a manual commit fallback. The former
experimental WebRTC route has been removed.

Microphone capture requires a secure browser context. Loopback HTTP is suitable for local testing;
use HTTPS for any non-loopback host. If the browser denies the microphone, confirm the page origin,
site permission, selected input device, and operating-system privacy setting. Use headphones to
avoid TTS audio being captured as new input.

If connection fails, check in order:

1. PostgreSQL is healthy on port `5433` and `GET /api/candidate/jobs` returns the seeded roles;
2. `GET /providers/health`, vLLM `/v1/models`, and Speaches `/health` are healthy;
3. the reasoning, STT, and TTS model IDs match the models available to those services;
4. the browser origin is the application origin and no unsupported relay query is present;
5. application logs for intake fallback, connect timeout, upstream rejection, or oversized message;
6. container networking (`host.docker.internal` when the app container reaches host providers).

Capacity rejection is not an answer-quality fallback. vLLM owns continuous batching and upstream
queueing; the application admission limit prevents unbounded work, but it must not be used to hide
a weak development engine. Calls above application admission return HTTP 429. They must be retried
with jitter/backoff or scheduled by a shared production queue, never reported as HTTP 200
deterministic interview decisions. Align the application limit with end-to-end latency and quality
gates, including speech queueing. The local default of four model calls is provisional overload
protection, not a validated four-interview SLO. `compose.load-test.yaml` raises it only during
saturation testing; the completed short-run results are in
[`LOAD_TEST_REPORT_VLLM_2026-09-03.md`](reports/LOAD_TEST_REPORT_VLLM_2026-09-03.md).

The current local path uses development authentication and system setup approval. Do not treat it
as production candidate admission. The relay still takes its destination and credential only from
server-side provider composition, and the workflow remains the authority for final transcript
handling and question progression. The bundled browser has no production WebSocket credential
flow; the edge rejects unauthenticated production realtime sessions rather than silently allowing
them. The selected `.env.example` sets admission to four; the network-free settings default is eight.
Neither number is a production interview-concurrency guarantee.

## Telemetry contract

`MetricRegistry` exposes a minimal Prometheus text contract with an explicit label allowlist.
`Tracer` exports metadata-only `SpanRecord` values through a vendor-neutral `SpanExporter` seam.
Use hashed tenant tags and opaque interview/turn IDs. Low-cardinality label candidates are
`capability`, `provider`, `model`, `outcome`, `reason`, `priority`, and `dimension`; do not use
interview IDs as metric labels.

Recommended counters and histograms:

- `interviewer_admission_attempts_total` and `interviewer_admission_rejections_total`;
- `interviewer_budget_warning_total` and `interviewer_budget_exceeded_total` by dimension;
- `interviewer_provider_latency_ms` by capability/provider/model/outcome;
- `interviewer_provider_usage_total` by capability/provider/model/unit;
- starts, completions, withdrawals, pauses, fallbacks, schema failures, and safety blocks.

Create spans for API requests, graph nodes, provider calls, schema validation, checkpointing, and
event publication. The collector deletes known content fields as a second line of defense, but the
application must exclude them first. Production must add authenticated telemetry transport,
network policy, retention policy, and an automated redaction canary.

## Run the hardware capacity campaign

Follow [`VLLM_LOAD_TESTING.md`](VLLM_LOAD_TESTING.md) in order. It is the authority for isolated
reasoning (with an optional approved same-family Q4/Q8 comparison), isolated GPU speech, combined
candidate-turn testing, fixed workloads, discarded warm-ups, concurrency sweeps, and tok/s/decimal-GB
VRAM normalization with GiB also recorded. Do not publish capacity from the
network-free benchmark below.

## Run the offline release gate

```powershell
uv run python -m agentic_interviewer.evaluation.harness `
  evaluation/fixtures/guardrails.json `
  --output evaluation/results/guardrails.json `
  --min-pass-rate 1.0
```

The output is deterministic in decisions but records machine-dependent wall-clock latency; it is
a smoke benchmark, not a capacity result. Do not commit results containing candidate data.

Exercise the normalized usage and timing path through the network-free fake adapter:

```powershell
uv run python -m agentic_interviewer.evaluation.benchmark `
  evaluation/fixtures/fake_reasoning_benchmark.json `
  --output evaluation/results/fake-reasoning.json
```

Token totals are deterministic. Latency and throughput are environment-specific diagnostic values
and must not be used as evidence for real provider or target-hardware capacity.

## Run the local monitoring stack

Review and update the image pins, then set a non-default password:

```powershell
$env:GRAFANA_ADMIN_PASSWORD = Read-Host -MaskInput "Grafana admin password"
docker compose -f compose.yaml -f compose.observability.yaml config
docker compose -f compose.yaml -f compose.observability.yaml up --build -d
docker compose -f compose.yaml -f compose.observability.yaml ps
```

Grafana and Prometheus bind only to loopback. OTLP ports also bind to loopback for host-side
development. The application currently has telemetry seams but does not install an OpenTelemetry
SDK/exporter; an SDK composition-root hook is still required before application signals appear.
Collector self-metrics prove only collector health.

## Release evidence still required

- target-hardware one- and two-session load results with p50/p95/p99 and peak RAM/VRAM;
- restart/resume, provider loss, privacy denial, duplicate delivery, backup/restore, and reconnect
  storm drills;
- authenticated multi-tenant enforcement and distributed limiter tests;
- alert delivery, dashboard review, on-call ownership, and SLO/error-budget approval;
- representative scoring, accessibility, fairness, transcript, and voice evaluation sign-off.

Runbook index: [capacity](runbooks/capacity.md), [budgets](runbooks/budgets.md), and
[telemetry](runbooks/telemetry.md).
