# Project Guide

## 1. Purpose and current status

This guide describes the current implementation and its operating boundaries.
[`../plan.md`](../plan.md) records broader product direction; historical design criteria are not
implementation claims. [`MILESTONES.md`](MILESTONES.md) separates current evidence from open work.

The project is intentionally delivered in gates. The repository now contains the offline foundation,
recruiter/candidate/reviewer API flow, a candidate browser interview, PostgreSQL-backed development
job/resume intake, durable interview-session persistence, local voice-provider adapters, operational
controls, and evaluation/monitoring assets. Mocked wire tests are not proof that a downloaded model,
GPU profile, production database, or monitoring deployment has passed. Check
[`MILESTONES.md`](MILESTONES.md) and the test suite for the exact evidence and remaining external
gates.

The system assists a human hiring team. It may conduct an approved interview and assemble evidence, but it may not infer protected characteristics, use voice/accent/emotion as a signal, silently change a rubric, fabricate evidence, rank candidates without validation, or issue a final hire/reject decision.

## 2. End-to-end ownership

```text
Composition -> selected provider ports and runtime lifecycle
                         |
Candidate text/audio -> API orchestration + admission/budgets
                         |                  |
                         |                  +-> selected STT/reasoning/TTS ports
                         v
Finalized text + validated analysis -> interview service -> deterministic graph
                                                            |
Approved facts/plan/rubric -----------------------------------+
                                                            |
                   intent policy -> answer-aware question / evidence
                                                            |
                   cited scoring -> human review -> approved export API
```

Ownership is deliberately split:

| Area | Owner | Must not own |
|---|---|---|
| API/session edge | Authentication, authorization, lifecycle, request validation, admission and orchestration of model calls | Model-specific payload construction |
| Workflow | Explicit state, routing, interrupts, budgets, termination | Unbounded agent discretion |
| Guardrail policy | Deterministic action from versioned rules and classified intent | Competency scoring |
| Capability router | Select capability/privacy-eligible routes and bounded fallback | Product rubric decisions or API admission quotas |
| Provider adapters | Translate domain requests, enforce supported controls/retry deadlines, normalize results/usage | Workflow state transitions |
| Runtime operations | Session budgets, model-call admission, circuit breakers and redacted telemetry | Provider payloads or interview scoring |
| Scoring | Fixed anchors applied to cited answer evidence | Guardrail/moderation metadata, voice traits |
| Human reviewer | Approve or override with a reason | Hidden model reasoning or unsupported claims |

Large audio and source documents stay outside graph state. Workflow state carries identifiers, compact transcript/evidence records, counters, versions, and risk events.

## 3. Repository map

The package and tests evolve with milestones; use `rg --files` for the authoritative current tree. The intended separation is:

```text
src/agentic_interviewer/
  domain/       Pydantic schemas, enums, capability protocols, normalized errors
  adapters/     Fake, generic OpenAI-compatible, and dedicated Speaches implementations
  composition/  Concrete provider selection and lifecycle wiring
  policy/       Intent classification, bounded guardrail decisions and response rules
  workflow/     Typed graph/state-machine nodes and routes
  api/          FastAPI edge, auth/lifecycle and provider-neutral browser voice transport
  services/     Provider-neutral interview/intake orchestration and runtime controls
  persistence/  SQLite interview sessions, Postgres candidate intake, optional graph checkpointing
  operations/   Admission, budgets, redacted telemetry, metrics
  evaluation/   Deterministic release gate and benchmark harness
  security/     Signed development token contract
tests/          Offline unit, conformance, architecture, and workflow tests
docs/           Operating guide and milestone evidence
```

Domain, workflow, and browser transport modules must not import provider SDKs or construct vendor
payloads. `adapters/openai_compatible.py` implements generic HTTP reasoning/audio contracts;
`adapters/speaches.py` owns Speaches buffered defaults and behavior;
`adapters/speaches_realtime.py` owns Speaches WebSocket negotiation and event translation.
`composition/providers.py` and settings/Compose select implementations. Provider-specific deployment
instructions, wire-conformance tests, and benchmark profiles may name providers; domain decisions
and candidate-facing events must not depend on those names.

## 4. Local development

### Prerequisites

- Python 3.12;
- `uv` available on `PATH`;
- Docker Desktop with the NVIDIA Container Toolkit/runtime for PostgreSQL and GPU Speaches;
- optional for model-backed evaluation: Docker Desktop with GPU passthrough, the pinned vLLM image, the Q4/Q8 reasoning profiles, an admitted Speaches transcription model, and Kokoro weights.

Install and verify the offline foundation:

```powershell
Copy-Item .env.example .env
uv sync --all-groups
docker compose up -d postgres speaches
docker compose -f compose.vllm.yaml --profile q4 up -d vllm-q4
uv run pytest
uv run uvicorn agentic_interviewer.api.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` for the candidate interview. The seeded PostgreSQL instance listens
only on `127.0.0.1:5433`; the application still listens on `127.0.0.1:8000`. The local provider
profile expects Speaches on `8001` and vLLM on `8080`.

Useful focused checks:

```powershell
uv run pytest tests/unit
uv run pytest tests/integration
uv run ruff check .
uv run python -m agentic_interviewer.evaluation.harness evaluation/fixtures/guardrails.json
```

Do not make the offline suite depend on model downloads, GPUs, provider availability, or internet access. Model conformance and benchmark runs are a separate, explicitly selected stage.

`tests/conftest.py` isolates pytest from a developer's live `.env` before collection: fake providers,
in-memory test storage, and development test authentication. Individual tests explicitly construct
alternate configurations. Running the offline suite must not call a locally running model or use
the development PostgreSQL catalog.

## 5. Configuration

Copy `.env.example` to `.env`. Keep secrets and candidate data out of Git. The example file documents the operational intent of each setting; the settings model in source code is authoritative for keys currently consumed.

The settings-model default profile is `fake`, which is deterministic and network-free. The checked-in
`.env.example` selects `local-specialized` for the manual browser flow. Interview session state
persists to SQLite by default; the seeded job catalog, uploaded resume bytes/text, and intake links
are stored in PostgreSQL when `DATABASE_URL` is set. LangGraph uses its in-memory saver in
development. Production can explicitly compile the graph with the optional Postgres checkpointer
and must run schema setup as a separate controlled operation. Set `PROVIDER_PROFILE=local-specialized`
for synthetic local trials only. Health probes establish reachability, not quality admission;
production requires the independent quality, latency, privacy, and capacity gates.

```text
DATABASE_URL=postgresql://interviewer:interviewer@127.0.0.1:5433/interviewer
PROVIDER_PROFILE=local-specialized
REASONING_PROVIDER_NAME=vllm
REASONING_BASE_URL=http://127.0.0.1:8080
REASONING_MODEL=mistral-7b-instruct-v0.3-q4
REASONING_REGION=local
REASONING_EXTERNAL_PROCESSING=false
REASONING_RETAINS_PROVIDER_DATA=false
REASONING_CHECK_HEALTH_ENDPOINT=true
REASONING_MAX_COMPLETION_TOKENS=1024
REASONING_TEMPERATURE=0.3
REASONING_TOP_P=0.9
REASONING_PRESENCE_PENALTY=0.0
SPEACHES_BASE_URL=http://127.0.0.1:8001
SPEACHES_STT_MODEL=Systran/faster-distil-whisper-small.en
SPEACHES_TTS_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX
SPEACHES_DEFAULT_VOICE=af_heart
```

For an admitted hosted speech endpoint, select `PROVIDER_PROFILE=openai-compatible` and configure
the `AUDIO_*` settings shown in `.env.example`. Hosted audio is still denied unless
`ALLOW_EXTERNAL_MODEL_PROCESSING=true`; provider retention additionally requires
`ALLOW_PROVIDER_DATA_RETENTION=true`. Restrict `ALLOWED_PROVIDER_REGIONS` where deployment policy
requires it. These are explicit deployment policy decisions, not properties inferred from a URL.

Generic live settings deliberately have no implicit reasoning/audio endpoint or model. Generic
synthesis additionally needs `AUDIO_DEFAULT_VOICE` or an explicit request voice; domain speech
requests do not select a provider's voice. `REASONING_REGION`/`AUDIO_REGION` default to `vendor`, and
the associated `*_EXTERNAL_PROCESSING` and `*_RETAINS_PROVIDER_DATA` metadata default to `true`.
Application policy still denies those routes unless permission is explicit. The local example
overrides the metadata to local/no-external-processing/no-retention. Never infer that metadata
from a loopback URL, because a local gateway can forward requests externally.

`REASONING_CHECK_HEALTH_ENDPOINT` defaults to false for generic endpoints because `/health` is
not an OpenAI-compatible requirement. The local deployment may explicitly enable it. The optional
legacy `REASONING_ENABLE_THINKING` setting is translated into a request extension only at composition;
leave it unset unless the selected endpoint supports that extension.

When the application itself runs in Docker, use `host.docker.internal` instead of `127.0.0.1` for providers running on the Windows host.

Budget defaults are development limits, not model suggestions. A deployed interview plan owns its exact duration, question, probe, token, audio, character, and cost limits. Crossing a hard limit closes safely or pauses for authorized review; it never silently raises the limit.

### API lifecycle and ownership

In development, `X-Tenant-ID` and `X-Actor-ID` create a local principal with all roles. This is for
synthetic testing only. With `AUTH_REQUIRED=true`, send a short-lived bearer token containing the
tenant, subject, and role claims. Recruiter/reviewer identity in audit records always comes from
the verified principal, never request JSON. Candidate-only tokens are bound to `candidate_id` and
receive a reduced response that excludes rubric anchors, scores, evidence, guardrail events, and
audit records.

| Order | Endpoint | Role | Result |
|---:|---|---|---|
| 0a | `GET /api/candidate/jobs` | candidate | List active seeded jobs without company-only questions |
| 0b | `POST /api/candidate/intakes` | candidate | Store a bounded resume, prepare/approve the question bank, and return a ready interview |
| 1 | `POST /interviews` | recruiter | Extract draft source claims, plan, and rubric |
| 2 | `PATCH /interviews/{id}/source-claims` | recruiter | Correct a draft resume/job claim with provenance |
| 3 | `POST /interviews/{id}/setup-approval` | recruiter | Freeze approved plan/rubric versions; status becomes `ready` |
| 4 | `POST /interviews/{id}/start` | bound candidate | Start the approved plan and return the first question |
| 5a | `POST /interviews/{id}/answers` | bound candidate | Submit a finalized typed answer |
| 5b | `POST /interviews/{id}/audio-answers` | bound candidate | Transcribe raw audio, record usage, then enter the same text workflow |
| 6 | `POST /interviews/{id}/speech` | bound candidate | Synthesize the current interviewer response |
| 7 | `PATCH /interviews/{id}/transcript` | bound candidate | Preserve original text and attach a correction |
| 8 | `POST /interviews/{id}/review/override` | reviewer | Replace scores with cited/insufficient-evidence scores and a reason |
| 9 | `POST /interviews/{id}/review` | reviewer | Approve the assessment |
| 10 | `POST /interviews/{id}/exports` | reviewer | Idempotently produce the approved export artifact |
| any completed state | `POST /interviews/{id}/appeals` | bound candidate | Open a human-review appeal |

Every mutating interview call is tenant checked. Audio and speech calls require `Idempotency-Key`; they also
pass through rate, concurrency, circuit-breaker, execution-control, and cumulative session-budget
checks. `GET /providers/health` probes configured provider models, while `GET /metrics` exposes only
allowlisted metadata. The candidate browser described below uses the same lifecycle: intake creates
and approves a bounded setup, start returns the first question, the server-mediated WebSocket
delivers a final STT turn, `/answers` advances the guarded workflow, and `/speech` synthesizes the
next question. Production ATS delivery remains a separate integration rather than hidden behavior
in this API.

`POST /interviews` accepts nested `planning_controls` and optional `company_questions`. Omitted
controls select the default buffered allocation (1 introduction + 5 resume + 5 job questions).
Caller budgets are schema-bounded and `max_total_questions` is additionally capped by the
organization `MAX_QUESTIONS` setting. Required company-question overflow fails closed instead of
silently dropping questions. The deterministic reference planner records focus allocation in each
generated question's provenance; a model-backed planner must preserve the same contract.

## 6. Running the inference providers

### vLLM: configured reasoning model only

The configured vLLM model provides local reasoning. It does not receive candidate audio and does
not synthesize interviewer audio; speech uses separate ports. The supplied benchmark configuration
compares same-family GPTQ W4A16 and W8A16 Mistral 7B variants through separate Compose
profiles, using the exact served model name returned by `/v1/models`.

```powershell
docker compose -f compose.vllm.yaml --profile q4 up -d vllm-q4
docker compose -f compose.vllm.yaml logs -f vllm-q4
```

Do not expose port 8080 publicly. A reasoning profile must pass structured-output conformance; an
HTTP 200 response with empty final content is a normalized failure, not a usable result. The
adapter sends an application-side completion cap and conservative sampling controls, so permissive
engine defaults do not govern interview requests. vLLM owns request scheduling and continuous
batching; application admission remains a bounded overload control, not a substitute for measuring
provider concurrency.

The full isolated Q4/Q8, speech, combined-pipeline, and tok/s/GiB-VRAM procedure is
[`VLLM_LOAD_TESTING.md`](VLLM_LOAD_TESTING.md). vLLM is used only behind `ReasoningPort` for
schema-bounded interviewer phrasing, evidence extraction, and scoring inputs. Its adapter must pass
structured-output, deadline, budget, and normalized-error conformance; audio capability is neither
required nor advertised.

### Speaches: STT and TTS

Speaches is the chosen local/development speech provider. Compose runs the official CUDA 12.6.3
image with an NVIDIA GPU reservation, CUDA `float16` Whisper inference, CUDA-first ONNX Runtime for
Kokoro, and a named Hugging Face cache volume. vLLM owns reasoning. Download the configured
STT and TTS models once through the model endpoints; the named volume preserves them across
container replacement. The service is private to loopback on port `8001` and uses
`restart: unless-stopped`. A production deployment may self-host Speaches through its dedicated
adapters or select generic OpenAI-compatible audio adapters for an admitted compatible vendor.
The domain contracts and workflow do not change; a URL change alone does not establish protocol
or privacy compatibility.

```powershell
docker compose up -d speaches
Invoke-RestMethod -Method Post http://127.0.0.1:8001/v1/models/Systran/faster-distil-whisper-small.en
Invoke-RestMethod -Method Post http://127.0.0.1:8001/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX
```

`WHISPER__TTL=-1` keeps both managers resident after their first request in the current Speaches
build; Kokoro currently reuses the Whisper TTL. This is intentional for the measured 16 GB local
profile because cold CUDA initialization is large. Recheck this implementation detail when the
Speaches image changes.

The Speaches realtime adapter uses deliberately turn-based semantics:

1. The media client appends audio chunks incrementally over the Realtime WebSocket; this is incremental audio ingress, not a promise of incremental transcript text.
2. With `server_vad`, Speaches detects the speech boundary and commits the input buffer. A client using manual turn detection explicitly sends the buffer commit instead.
3. Transcription begins for the committed turn. The application consumes the completed transcription as the authoritative current-turn text; it does not depend on stable word-by-word partials.
4. The reasoning workflow runs only after that final transcript is available. Candidate corrections preserve both original and corrected provenance.
5. The implemented TTS path buffers the complete WAV before browser playback. Streamed TTS is
   deferred and must not be inferred from upstream API support.

These events follow the [Speaches Realtime API](https://speaches.ai/usage/realtime-api/) and its
OpenAI-compatible buffer/turn vocabulary inside the dedicated realtime adapter. The separate
buffered HTTP path uses `/audio-answers` to submit a completed turn to `/v1/audio/transcriptions`, and
`/speech` buffers `/v1/audio/speech` before responding. It therefore declares live input, partials,
server VAD, manual commit, streamed output, and barge-in as unsupported. Realtime transcription has
its own domain port and normalized application events; it does not change the buffered capability
flags. Provider compatibility is verified by conformance tests, not inferred from the label alone.

The MVP does not require interruption/barge-in readiness. While interviewer audio is playing, the client may keep input gated or require an explicit stop/repeat action. Full-duplex cancellation, echo handling, and mid-response truncation remain deferred.

### Candidate browser interview

The former Voice Lab and its experimental WebRTC signaling route have been removed. The application
root now serves the candidate flow:

```text
GET  /                              candidate interface
GET  /api/candidate/jobs            active job catalog
POST /api/candidate/intakes         multipart resume + selected job
WS   /ws/voice/realtime             same-origin application transcription protocol
```

Device preparation overlaps intake; speaking turns remain sequential:

1. The candidate selects a seeded job and uploads a PDF, UTF-8 TXT, or Markdown resume. The API
   enforces the configured byte limit, safe extension, PDF page/encryption rules, and extracted-text
   bounds before storing the immutable upload and text in the development catalog.
2. The browser moves immediately to device check before awaiting the intake response. Meanwhile,
   the API extracts bounded resume claims and named job requirements with source identifiers and
   spans, then creates the default 11-question bank. With a non-fake provider profile it sends only
   those bounded source items to the reasoning port and asks for a strict
   `introduction`/`resume`/`job` JSON object. The quality gate verifies allocation, same-kind source
   references, source overlap, standalone open wording, uniqueness, placeholders, and prohibited
   topics. Development retains rejected-plan status/failure codes in intake metadata, but the
   candidate UI hides those internals and automatically acknowledges the development fallback.
   Production returns 503 and never releases a fallback plan. This development shortcut is not
   production candidate admission or recruiter approval.
3. The device step requires an explicit microphone check. Camera permission is never requested
   automatically; the optional preview is local to the browser and its track stops before the
   interview begins.
4. Begin waits for both microphone and intake readiness, then connects the same-origin application
   WebSocket. The selected realtime adapter negotiates transcription and server VAD with automatic
   provider responses disabled. After each TTS question ends, the browser automatically streams
   24 kHz mono PCM16. The adapter commits after the configured
   silence interval; **Done speaking** flushes and commits manually if endpoint detection stalls.
5. Only the completed transcription event is submitted to `/answers`. The guarded workflow treats
   repeat, clarification, candidate questions, technical problems, refusals, insufficient answers,
   and unintelligible transcripts as explicit non-scoring dispositions. Repeat/clarification retain
   the question; explicit skip/do-not-know advances it. An unusable answer receives one retry before
   the next unusable response advances without evidence. Accepted substantive answers may trigger
   a grounded follow-up. `/speech` synthesizes the returned interviewer response as WAV; acoustic
   verbatim fidelity still needs model conformance. Typed input is the explicit STT degradation path.

The API edge enforces same-origin, bounded query/message policy and delegates to the composed
realtime transcription port. The provider adapter constructs its upstream URL, adds its server-only
credential, and translates provider events. `REALTIME_CONNECT_TIMEOUT_SECONDS`,
`REALTIME_VAD_THRESHOLD`, and `REALTIME_SILENCE_DURATION_MS` are bounded deployment controls.
The browser never receives provider credentials, chooses an upstream URL, or sends a
provider-specific session payload.

Only a bounded `language` query is allowed; model overrides are rejected. Browser commands are
`audio.append`, `audio.commit`, and `audio.clear`. Responses are `voice.connected`, `voice.ready`,
`voice.speech.started/stopped`, `voice.transcript.delta/completed` (with `text`), and sanitized
`voice.error`. `voice.ready` follows successful upstream configuration, not merely socket opening.

The realtime edge enforces the configured bearer/candidate-role authorization and fails closed
without it when authentication is required. The bundled
browser does not yet provide a production WebSocket credential flow; a page loading successfully
does not imply it can establish an authenticated production voice session.

Browser microphone capture is available only in a secure context. Current browsers treat loopback
`http://localhost` and `http://127.0.0.1` as trustworthy for local testing; use HTTPS for a remote
host. Grant microphone permission only to the expected origin. Headphones reduce the feedback loop
in which TTS playback is recaptured by STT.

The speech adapter must prove:

- literal turn-final STT, silence/no-invention, technical-term, malformed-audio, VAD/manual-commit, and deadline behavior;
- exact/verbatim synthesis behavior required by the question-delivery contract;
- accepted voice, language, format, and sample-rate values;
- maximum character and deadline enforcement;
- incremental audio ingress for realtime transcription; streamed TTS only if later implemented
  and capability-declared;
- normalized failures, latency, synthesized-character usage, and idempotency behavior.

The combined local profile runs Whisper, Kokoro, and the selected vLLM Mistral variant on the 16 GB
GPU. Runtime
logs must show `inference_device='cuda'` for Whisper and `CUDAExecutionProvider` before CPU for
Kokoro. Measure isolated service placement before the combined profile and preserve both results
under [`VLLM_LOAD_TESTING.md`](VLLM_LOAD_TESTING.md); do not infer capacity from weight size alone.

### Provider readiness

Before changing `PROVIDER_PROFILE` away from `fake`:

1. Confirm the provider endpoints are reachable only from the intended host/network.
2. Run the shared adapter conformance suite.
3. Run literal STT, verbatim TTS, and structured-reasoning fixtures.
4. Measure one-session latency and peak RAM/VRAM.
5. Measure two-session interleaving, queue time, and admission behavior.
6. Record model, quantization, projector, provider build, prompts, adapter version, and hardware with results.
7. Keep text fallback enabled.

## 7. Capability contracts and routing

The application keeps the three model capabilities separate, with a distinct transport contract
for realtime transcription:

- `ReasoningPort.generate(request)` returns schema-validated reasoning output and normalized provenance;
- `TranscriptionPort.transcribe(request)` returns the completed current-turn transcript and normalized provenance;
- `SpeechSynthesisPort.synthesize(request)` returns normalized audio events.

`RealtimeTranscriptionPort` in `domain/realtime.py` additionally owns a session lifecycle,
admitted audio/control input, and normalized readiness/speech/final-transcript/error events.
It is not implemented by claiming that
the buffered `transcribe` operation supports a live stream.

Every request supplies execution controls such as deadline, queue allowance, retry limit, priority, idempotency key, size/token/audio limits, structured/verbatim requirements, fallback permission, allowed regions, external-processing permission, and retention policy. Speech controls independently express live audio ingress, dependable partial transcripts, server VAD, manual commit, streamed audio output, barge-in, and timestamps. A route that supports audio chunks but not stable partial text can therefore be admitted for the MVP without overstating its capabilities.

Those execution controls describe buffered model requests. The realtime session contract is
separate and currently enforces configured connection/message/VAD controls and composition-time
privacy eligibility; it does not yet implement equivalent per-interview cumulative audio accounting
or distributed socket admission. Fake and generic audio profiles do not implicitly create a
Speaches realtime session.

An adapter must enforce a requested control, translate it to a tested equivalent, or reject the request as unsupported. It must never ignore the control. A route is eligible only when its declared capabilities satisfy every mandatory control plus remaining privacy, deadline, and budget policy.

Fallback is bounded: at most the policy-approved retry/failover path, with the same privacy and capability requirements. Provider errors are normalized so workflow code can distinguish timeout, unavailable, invalid response, unsupported capability, policy denial, and exhausted budget without knowing provider payloads.

## 8. Workflow and guardrails

The interview uses a bounded conversational schedule:

1. Load the immutable approved plan, control vectors, and remaining budgets. The default approved
   base bank contains one introduction, five resume questions, and five job questions; configured
   company-required questions replace or expand that allocation according to explicit policy.
2. Select the next base question deterministically. Optionally render its wording through the
   reasoning port, validate it, and deliver it through TTS or text with an idempotency key.
3. Stream PCM16 through the same-origin realtime transcription contract. Server VAD commits a finalized
   transcript; **Done speaking** is the manual fallback and dependable partial STT is not required.
4. Classify explicit repeat, clarification, move-on, abuse, technical, and guardrail intents through
   deterministic policy. Analyze a normal answer into the validated `accept`, `follow_up`,
   `insufficient`, `gibberish`, or `skip` disposition before evidence extraction.
5. For an accepted answer, extract cited evidence and update provisional rubric data. Ask one
   concise, grounded same-competency follow-up when it would obtain a material missing detail;
   otherwise move to the next prepared base question.
6. Give an unusable response one neutral retry. A second unusable response, explicit do-not-know,
   move-on request, or the 90-second question deadline skips the question and advances. Silence
   receives one spoken nudge after 12 seconds without resetting that deadline.
7. Keep runtime follow-ups tied to their parent question and total budget; they never create a new
   unapproved competency or trap the candidate in an unbounded retry loop.
8. Adaptive questions live in a separate runtime bank with parent-question provenance; they never
   mutate the immutable recruiter-approved base plan or silently change its version.
9. After all scheduled questions drain, score from fixed anchors and cited evidence, calculate aggregates in
   ordinary code, and interrupt for mandatory human review before finalization/export.

Repeat, clarification, accessibility, explicit stop, unintelligible input, guardrail action,
provider failure, and hard-budget handling are immediate exceptions. They are control events, not
adaptive questions. A later consistency probe compares substantive claims only; it is not lie
detection and must not use hesitation, accent, emotion, or other voice characteristics.

Important policy behavior:

- repeat and clarification requests receive neutral help and no penalty;
- answer-seeking, manipulation, derailment, or injection receives a bounded neutral redirect;
- explicit stop ends promptly;
- uncertain classifier output cannot be interpreted as misconduct;
- severe safety/security events can pause or close according to the versioned policy;
- guardrail events remain auditable but are excluded from scoring inputs;
- model-generated output is validated before candidates see it.

At most two neutral redirects for the same question and one subsequent warning are the initial plan defaults. The configured repeated-event threshold then pauses or closes safely. These counters belong to application state, not the model.

## 9. Testing strategy

### Always-offline tests

- Pydantic validation and serialization;
- import-boundary enforcement;
- fake unified and specialized adapters producing normalized equivalent results;
- routing allow/deny/fallback tables and mandatory-control rejection;
- budget boundaries and retry amplification;
- guardrail golden cases, redirect counters, stop handling, and false positives;
- workflow traversal, interruption/resumption, and idempotency;
- negative score-isolation tests proving guardrail/moderation labels never reach scoring.

Run with:

```powershell
uv run pytest
```

### Model-backed conformance and benchmarks

Run only when explicitly enabled and providers are local/approved. Keep them out of the default test command. Cover:

- exact provider wire compatibility;
- schema output validity and retry behavior;
- literal STT fidelity, silence handling, and correction workflow;
- verbatim TTS plus supported formats;
- latency, queue time, tokens/audio/characters, RAM/VRAM, and failure behavior;
- one and two concurrent sessions;
- provider outage and text degradation.

Passing health checks does not replace these tests. Store benchmark outputs without candidate PII or unapproved raw audio.

## 10. Docker operation

Start only the seeded PostgreSQL catalog when running Python and both model providers on the host:

```powershell
docker compose up -d postgres
uv run uvicorn agentic_interviewer.api.app:app --reload --host 127.0.0.1 --port 8000
```

Build and run the application, PostgreSQL, and persistent GPU Speaches service in containers:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Inspect it:

```powershell
docker compose ps
docker compose logs -f app
Invoke-RestMethod http://127.0.0.1:8000/health
```

Stop containers without deleting data volumes:

```powershell
docker compose down
```

The Compose stack publishes PostgreSQL only on `127.0.0.1:5433` and Speaches only on
`127.0.0.1:8001`, initializes the job/resume/intake
schema from `ops/postgres/init`, and waits for database health before starting the application. The
application image installs locked runtime dependencies with uv, copies source after dependency
installation for better rebuild caching, runs as a non-root user, and exposes port `8000`.
Speaches model files live in `speaches-model-cache`; vLLM model files live in the separate
`vllm-model-cache`. This keeps both model families out of the application image.

Compose is a developer convenience, not proof of production readiness. The application includes signed-token, tenant-query, rate/admission, retention, audit, redaction, and Postgres-checkpointer foundations. Production still requires OIDC/managed keys, database RLS, private networking, object storage, malware scanning, transactional event delivery, backups, a deployed OTLP exporter, and restore/chaos drills.

## 11. Data, privacy, and audit rules

- The development intake stores resumes up to 5 MB in PostgreSQL `bytea` so the manual flow is
  self-contained. Production must move document bytes to encrypted object storage and keep only
  identifiers, hashes, extraction status, and policy metadata in PostgreSQL.
- Treat resume text, job documents, transcripts, and candidate messages as untrusted data, never instructions.
- Redact or exclude protected and irrelevant attributes from planning where practical.
- Default to no raw-audio retention after transcript confirmation; actual retention requires explicit policy, consent, encryption, and deletion enforcement.
- Never place candidate text or PII in metric labels.
- Operational logs and immutable business audit records are separate concerns.
- Record plan/rubric/prompt/policy/model/adapter versions and normalized usage for consequential outputs.
- Support transcript correction and preserve original/corrected provenance according to retention policy.
- Export only a human-approved artifact, never hidden chain-of-thought.

## 12. Failure and degradation

| Failure | Required behavior |
|---|---|
| Low-confidence or materially incorrect STT | Ask for correction/repeat; do not score until final |
| STT unavailable | One eligible fallback, then typed input/reconnect |
| TTS unavailable | Display the approved question as text |
| Reasoning timeout | Bounded retry or approved fixed question |
| Unsupported control | Reject route; never silently relax it |
| Candidate disconnect | Preserve resumable state within the configured TTL |
| Capacity unavailable | Do not begin an undisclosed degraded voice interview |
| Classifier uncertain/unavailable | Deterministic neutral handling; never infer misconduct |
| Scoring unavailable | Complete interview, queue/retry later, require human review |
| Hard budget reached | Close safely or pause for authorized override |

## 13. Milestone sequence

- **M0:** contracts, schemas, fake adapters, router/budgets, guardrails, workflow skeleton, score isolation, offline acceptance.
- **M1:** text-only preparation/interview/review slice with durable state and audit.
- **M2:** provider-neutral reasoning plus Speaches STT/TTS, transcript correction, degradation,
  conformance, and hardware benchmarks. The current reasoning benchmark implementation uses vLLM.
- **M3:** grounded question-bank contracts and preparation controls, followed by answer-aware
  delivery and the candidate upload/device/interview browser slice.
- **M4:** durable turn analysis, claim ledger, TTS prefetch, idempotent turn commands, and
  out-of-order/stale-result protection for a scaled deployment.
- **M5:** production routing, security/privacy hardening, queues/admission, restore/chaos and operations.
- **M6:** quality pilot, calibration, accessibility/fairness review, canary/rollback, appeal and controlled release.

The actionable checkpoints and acceptance evidence for these milestones are in
[`BUFFERED_INTERVIEW_IMPLEMENTATION_PLAN.md`](BUFFERED_INTERVIEW_IMPLEMENTATION_PLAN.md).

The live connection, VAD, speaking-turn, silence, retry, and degradation decisions are consolidated
in [`VOICE_ARCHITECTURE_DECISIONS.md`](VOICE_ARCHITECTURE_DECISIONS.md). Deployment economics and
the dated market snapshot is in
[`INFERENCE_DEPLOYMENT_ANALYSIS.md`](INFERENCE_DEPLOYMENT_ANALYSIS.md); metric definitions, planning
ranges, production SLOs, and capacity formulas are in
[`PERFORMANCE_AND_CAPACITY_ESTIMATES.md`](PERFORMANCE_AND_CAPACITY_ESTIMATES.md).

The shipped local profile is a synthetic-development configuration, not an admitted production
model. The retained calibration failed quality and combined latency gates; repeat the documented
conformance and sustained tests on any changed model or host before making capacity claims.

## 14. Known limitations

- The interactive runtime performs answer analysis synchronously on the candidate-visible path.
  Durable reasoning jobs, answer revisions, stale/out-of-order result rejection, a claim ledger,
  and TTS prefetch remain open production-hardening checkpoints.
- The supplied Mistral 7B variant is configured for reasoning only and has not passed production
  quality admission; speech remains behind separate provider-neutral contracts.
- Quantized weight size is not total runtime memory; KV cache, context, compute buffers, Speaches
  models, and concurrent requests all add overhead.
- Turn-based sequential voice is the intended first implementation; full-duplex interruption/barge-in is deferred.
- Realtime transcription uses a dedicated adapter/port, while the HTTP audio adapter is buffered
  and intentionally reports Realtime capabilities as false. The candidate browser uses normalized
  same-origin events with server VAD and a manual commit fallback; this does not change the HTTP
  adapter's capability declarations. The interview relies on committed-turn final
  transcription rather than dependable partial captions.
  Timestamps and usage fields must be capability-declared and tested, not assumed from an
  OpenAI-compatible route.
- Model quality, scoring calibration, accents/noise, fairness, accessibility, and concurrency require representative evaluation before real hiring use.
- Container configuration alone does not provide production durability or security controls.
- The candidate intake auto-approves the prepared setup and development authentication grants all
  roles. Production must replace both with tenant-bound invitations, candidate-only identity, and
  an explicit recruiter approval policy.
- Human review/export gates, development authentication, retention enforcement, and repository tenant isolation are implemented and tested. Continue using only synthetic data until production identity, storage, privacy, fairness, accessibility, security and operational sign-offs pass.

## 15. Change checklist

Before merging a new provider or workflow path:

1. Keep vendor types behind the adapter boundary.
2. Declare capabilities and fail closed on unsupported mandatory controls.
3. Add deterministic tests and provider conformance tests separately.
4. Prove no guardrail, moderation, protected-trait, or voice metadata reaches scoring.
5. Bound retries, costs, tokens, audio, synthesis, questions, probes, and elapsed time.
6. Preserve idempotency and resumability for side effects.
7. Document configuration, degradation, privacy behavior, and measured limitations.
8. Update [`MILESTONES.md`](MILESTONES.md) with evidence, not aspiration.
