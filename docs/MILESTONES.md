# Implementation status and release gates

This checklist describes the current repository, not the original two-pass interview design.
[`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) explains current behavior;
[`VOICE_ARCHITECTURE_DECISIONS.md`](VOICE_ARCHITECTURE_DECISIONS.md) records the transport decisions.
[`BUFFERED_INTERVIEW_IMPLEMENTATION_PLAN.md`](BUFFERED_INTERVIEW_IMPLEMENTATION_PLAN.md) retains
historical milestones whose base-first/deferred-probe criteria are superseded.

Test names below identify regression coverage. Run the offline suite for the current command
result; a test's presence is not evidence of live provider conformance or production readiness.

## Implemented foundation and conversation

| Area | Current implementation | Regression coverage |
|---|---|---|
| Domain contracts | Typed reasoning, buffered transcription, synthesis, controls, usage and errors | `test_domain_models.py`, `test_fake_adapter.py`, `test_router.py` |
| Provider boundaries | Concrete providers selected in `composition/providers.py`; generic HTTP, Speaches buffered and Speaches realtime adapters separate | `test_architecture.py`, `test_live_adapters.py`, `test_voice_realtime.py` |
| Provider-neutral realtime | `domain/realtime.py` session contract; API/browser exchange application events rather than provider payloads | `test_voice_realtime.py` |
| Approved base plan | Default 1 introduction + 5 resume + 5 job questions; bounded company/focus controls and source provenance | `test_buffered_interview.py`, `test_question_planning.py` |
| Answer-aware delivery | Validated accept/follow-up/insufficient/gibberish/skip decisions; bounded immediate follow-up then return to base plan | `test_turn_analysis.py`, `test_buffered_interview.py` |
| Control and non-answer handling | Repeat/clarify retain; explicit skip advances; one unusable-answer retry; no evidence from control events | `test_guardrails.py`, `test_buffered_interview.py`, `test_workflow.py` |
| Browser preparation | Immediate device step while intake request runs; microphone required, camera optional; Begin gated on readiness | `test_candidate_ui_flow.py`, `test_candidate_interview_flow.py` |
| Sequential voice | Automatic capture after TTS, server VAD/manual commit, final-turn transcription, typed/text degradation | `test_voice_realtime.py`, `test_candidate_interview_flow.py` |
| Timing and spoken transitions | One 12-second silence nudge, 90-second question deadline, bounded repeat/retry/move-on language | `test_candidate_ui_flow.py`, `test_buffered_interview.py` |
| Evidence and human review | Transcript-linked scoring fixture, reviewer override/approval, appeal, idempotent export | `test_workflow.py`, `test_api.py` |
| Development durability | SQLite session persistence, tenant queries, retention/legal hold, audit chain; PostgreSQL intake catalog | `test_persistence.py`, `test_api.py`, `test_candidate_interview_flow.py` |
| Operational controls | In-process admission/rate/budget/circuit-breaker controls and redacted telemetry seams | `test_operations_controls.py`, `test_provider_runtime.py`, `test_telemetry.py` |
| Reproducible evaluation | Fixed synthetic fixtures, network-free release tests, live benchmark runner and saved-result validator | `test_evaluation_harness.py`, `test_benchmark_harness.py`, `test_standard_benchmark.py` |

The deterministic scoring fixture is not a validated hiring assessment. The bundled browser uses
development authentication and system setup approval, including automatic acknowledgment of
development fallback planning. Production intake fails closed on failed/rejected model planning.

## Provider configuration and safety boundaries

- Core domain, workflow, services, policy and operations do not select providers, embed provider
  defaults, or use provider network clients. API transport depends on ports and composition.
- Speaches model/voice defaults and protocol quirks belong to its dedicated adapters. Generic
  OpenAI-compatible adapters require explicit endpoint/model configuration and a configured or
  per-request synthesis voice.
- Generic live-provider privacy defaults are conservative: external processing and retention are
  denied by application policy until explicitly allowed. The selected local deployment declares
  local/no-external-processing/no-retention metadata in configuration, not by guessing from a URL.
- The realtime API fails closed when production authentication is required; the bundled browser
  does not yet implement a production socket credential flow.
- The selected example limits model-call admission to four; settings alone default to eight.
  These per-process values are not distributed quotas or measured interview SLOs.

## Historical calibration evidence

The [retained report](reports/LOAD_TEST_REPORT_VLLM_2026-09-03.md) records the 2026-09-03/04 short-run
vLLM/speech campaign and its final validation: 174 offline tests passed at that point. Earlier
foundation totals (58, 88, 104 and 117) are superseded historical snapshots, not current totals.

The campaign measured isolated and combined concurrency ladders. Both reasoning variants achieved
only 70% disposition correctness, and the combined path failed latency gates despite error-free
requests through concurrency four. No production-admitted or sustained maximum concurrency was
established. Q8 results were left unchanged after the user limited further testing.

Generated benchmark JSON, caches, and old reports were deleted at the user's request on 2026-09-04.
The retained report is a historical summary, not a pointer to available raw evidence. Tests,
benchmark scripts, fixtures and configuration remain; future measurements need a new run directory.

## Open work, not current guarantees

- [ ] Production identity/invitations, authenticated tenant/interview-bound realtime sockets,
  explicit recruiter approval, and reviewer UI.
- [ ] Distributed admission/quotas, durable queues/outbox, Postgres application/checkpoint
  composition, process-kill resume, duplicate/stale/out-of-order delivery drills.
- [ ] Durable answer revisions/analysis jobs and cross-turn claim ledger; any asynchronous work
  must preserve answer-aware question decisions rather than revive blind base-bank draining.
- [ ] TTS prefetch/cache and admitted streaming playback, cancellation, reconnect and failure-isolation
  evidence. Current HTTP TTS buffers the full response.
- [ ] Deployed authenticated telemetry exporter, verified alerts/dashboards, backups/restore,
  malware scanning, encrypted object storage, managed keys and database RLS.
- [ ] Representative STT/noise/accent and reasoning quality evaluations, scoring calibration,
  accessibility/fairness review, legal/privacy/security sign-offs.
- [ ] Sustained target-hardware latency/quality/throughput tests, failure-domain and overload drills,
  cost validation, controlled pilot, canary and rollback approval.

Full-duplex audio, WebRTC, natural barge-in, direct browser-to-model sessions, and dependable
word-by-word transcript streaming are deferred, not prerequisites of the implemented sequential
MVP. No autonomous hire/reject decision or voice-trait scoring is permitted.
