# Failure and challenge register

This register records product and architecture failures that can recur even when every required
service is running correctly. Each entry must state what happened, why the design allowed it, the
resolution, and an objective prevention checkpoint. Operator omissions such as forgetting to start
an endpoint are runbook matters and do not belong here.

## Question preparation and release quality

### Generic, ungrounded questions reached a live interview

- **What happened:** Questions referred to numbered “resume topics” and “requirement areas” instead
  of naming an actual project, claim, skill, or job requirement.
- **Why the design failed:** The planning contract checked question counts and prohibited phrases,
  but did not require traceable source claims, standalone clarity, specificity, or semantic
  uniqueness.
- **Resolution:** Build questions from extracted, source-linked resume claims and named job
  requirements; reject any bank that cannot demonstrate grounding and standalone clarity.
- **Prevention checkpoint:** No interview can become ready unless every resume/job question carries
  a valid source reference and passes grounding, specificity, uniqueness, and clarity evaluations.

### Planning degraded silently into a low-quality interview

- **What happened:** A failed or rejected model-backed plan was replaced without making the quality
  change prominent before the interview began.
- **Why the design failed:** Availability was treated as more important than the integrity of the
  question bank. Provider exceptions were collapsed into a fallback path without a stable failure
  code or a release gate.
- **Resolution:** Normalize failures into PII-free codes, count them in telemetry, preserve planning
  mode and degraded status in intake metadata, and fail closed in production. The current synthetic
  browser flow hides planning internals and automatically acknowledges development fallback while
  device checks proceed; this is not an explicit candidate consent or recruiter quality gate.
- **Prevention checkpoint:** Integration tests verify response status/code, redacted metrics and
  production refusal. UI tests verify immediate device check and no internal preparation copy.
  Candidate resume text and raw provider output must never appear in telemetry. Production still
  needs accountable approval and quality admission before an interview begins.

This is intentionally recorded as the systemic silent-degradation problem, not as a lesson about a
particular deterministic implementation.

### Structured planning exhausted its budget without a final answer

- **What happened:** A planning request returned HTTP 200 but spent the completion budget on hidden
  reasoning and supplied no final structured content.
- **Why the design failed:** The adapter did not set an explicit thinking policy for a bounded JSON
  task, and it reduced an empty final answer and its finish reason to a generic invalid-plan error.
- **Resolution:** Send the provider-supported non-thinking control for bounded structured planning,
  reject an empty final payload explicitly, and record only safe finish-reason/empty-final telemetry.
- **Prevention checkpoint:** A live structured-output conformance test verifies non-empty valid JSON,
  bounded latency and token use, and distinct PII-free telemetry for empty-final responses. It must
  run against each admitted reasoning profile before release.

### A grammatically valid open prompt was rejected by the quality gate

- **What happened:** The model produced a grounded introduction beginning with an imperative such
  as "Tell me...", but the planner rejected it solely because it ended with a period rather than a
  question mark.
- **Why the design failed:** The quality gate confused punctuation with conversational intent. An
  imperative interview prompt can be complete and open-ended without being written as an
  interrogative sentence.
- **Resolution:** Accept either an interrogative ending or an allowlisted open-prompt construction,
  while retaining minimum substance, grounding, prohibited-topic, placeholder, and duplication
  checks.
- **Prevention checkpoint:** Contract tests include valid imperative prompts and reject short,
  closed, placeholder, and ungrounded text independently of punctuation.

## Conversation control

### Candidate control requests were consumed as answers

- **What happened:** Requests to repeat or explain a question advanced the interview, as did “no,”
  “I don’t know,” and candidate questions about unclear wording.
- **Why the design failed:** The turn classifier defaulted unmatched non-empty text to an answer and
  the workflow had no first-class repeat, clarification, skip, unintelligible, or technical-problem
  dispositions.
- **Resolution:** Route each final transcript through a bounded turn-disposition contract with
  `INSUFFICIENT_ANSWER`, `REFUSAL_OR_SKIP`, `UNINTELLIGIBLE`, and `CANDIDATE_QUESTION` states.
  Repeat and clarification preserve the current question; explicit skip/do-not-know advances
  without evidence. An unusable answer takes one non-scoring retry; the next unusable answer moves
  on. Substantive answers are analyzed before a follow-up or next base question is selected.
- **Prevention checkpoint:** Transcript regression tests prove that control/non-answer turns do not
  create evidence, repeat/clarification retain the question, and skip/exhausted retries advance it.

### Clarification could not use the question's source context

- **What happened:** When a candidate asked what a numbered requirement meant, the interviewer
  could neither name nor explain it.
- **Why the design failed:** Questions were stored as display text without the source context and
  rationale required to rephrase them safely.
- **Resolution:** Keep source claim/requirement identifiers, source excerpts, and interviewer focus
  with each question. Handle repeat as raw replay and clarification as a separate bounded
  source-grounded explanation that does not supply the candidate's answer.
- **Prevention checkpoint:** Every planned question supports a source-grounded explanation that
  preserves intent and does not introduce a new assessment criterion.

## Evidence and adaptive behavior

### Non-answers and transcription garbage became evidence

- **What happened:** Very short refusals and a long gibberish transcript were treated as completed
  answers and influenced later questioning.
- **Why the design failed:** Non-empty text was accepted with maximum confidence; relevance,
  intelligibility, and substance were not validated before evidence extraction.
- **Resolution:** Add a bounded answer-quality gate before evidence creation and keep transcript
  confidence separate from evidence confidence.
- **Prevention checkpoint:** “No,” “I don’t know,” unrelated text, and gibberish produce zero
  evidence, zero scoring input, and zero adaptive prompts.

### Adaptive follow-ups were generic and repeated

- **What happened:** The same “specific trade-off” follow-up appeared repeatedly despite no usable
  earlier example.
- **Why the design failed:** Any non-empty base answer could trigger a template follow-up, while
  deduplication was scoped too narrowly and no originating claim span was required.
- **Resolution:** Generate follow-ups only from concrete claims, missing evidence, or real
  contradictions; store the triggering answer claim and answer fingerprint as provenance, then
  deduplicate by claim meaning across all base questions.
- **Prevention checkpoint:** Every adaptive question is unique, traceable to substantive evidence,
  and impossible to generate from a refusal, clarification request, or unintelligible transcript.

## Voice interaction

### Voice readiness depended on an incompatible session acknowledgement

- **What happened:** The answer control remained disabled although the browser connection existed.
- **Why the design failed:** Client readiness depended on a provider session update containing an
  unsupported turn-detection shape, and the rejected negotiation was not surfaced as a usable UI
  error.
- **Resolution:** Negotiate the provider-supported server-VAD contract and enable automatic capture
  only after the proxy confirms the session; retain manual commit and typed-answer fallbacks.
- **Prevention checkpoint:** A live WebSocket conformance test covers session negotiation, automatic
  post-TTS capture, silence commit, and visible failure state.

## Runtime failure codes

Planning responses and metrics use only these bounded codes:

| Code | Meaning |
|---|---|
| `planning_capacity_rejected` | Planning admission capacity was unavailable. |
| `planning_budget_exceeded` | The configured planning budget was exhausted. |
| `planning_empty_final_response` | The provider returned no final answer content. |
| `planning_provider_invalid_response` | The provider response could not satisfy its transport contract. |
| `planning_output_rejected` | Output failed the question-bank contract or quality gate. |
| `planning_provider_failed` | The planning provider failed without a safer specific category. |

Codes are operational categories, not candidate attributes. Do not add exception messages, model
output, resume text, job text, names, interview IDs, or tenant IDs to metric labels.
