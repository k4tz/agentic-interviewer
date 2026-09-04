# Security, privacy, and prohibited use

This project is an interview assistant, not an autonomous hiring authority. Production requires
human recruiter approval of the plan/rubric and human review before assessment export. The current
candidate intake uses system setup approval for synthetic development; it is not the production
recruiter workflow. Production use requires jurisdiction-specific legal and privacy review.

## Data classification

| Class | Examples | Default handling |
|---|---|---|
| Restricted candidate data | Resume, transcript, raw audio, corrections | Tenant scoped, encrypted in production, never telemetry |
| Consequential assessment | Evidence spans, scores, overrides, appeals | Immutable versions, audit chain, human approval |
| Security data | Token secret, provider credentials | Environment/secret manager only, never stored in session state |
| Operational metadata | Latency, capability, model, outcome | Allowlisted labels; tenant ID is hashed for traces |
| Public configuration | Limits, supported formats, policy versions | Version controlled unless it reveals infrastructure details |

Raw audio should not be retained after transcript confirmation unless explicit consent and policy
require it. SQLite is a local-development store; production needs encrypted PostgreSQL/object
storage, row-level tenant controls, managed keys, backups, and deletion verification.

## Threat model and controls

| Threat | Control in this repository | Production extension |
|---|---|---|
| Cross-tenant access | Signed tenant claim, API tenant check, tenant-scoped repository tests | OIDC, database RLS, distributed authorization tests |
| Prompt injection/coercion | Versioned intent policy, bounded redirects, score isolation | Model-backed classifier eval and human escalation |
| Answer or rubric leakage | Approved questions, no guardrail data in scorer/export | Output classifier and reviewer UI tests |
| Replay/duplicate side effects | Idempotency keys, optimistic revisions, idempotent export | Transactional outbox and consumer deduplication |
| Audit tampering | Per-tenant SHA-256 hash chain | Append-only/WORM storage and independent verification |
| Provider privacy violation | External-processing, region, retention, fallback controls | Contract-specific DPA and route admission registry |
| Resource exhaustion | Request limits, token bucket, concurrency admission, session budgets | Shared limiter, WAF, queue quotas and autoscaling |
| Telemetry data leak | Attribute/label allowlists and content-field rejection | Redaction canary and authenticated OTLP transport |
| Malicious upload | Bounded bytes/text, extension and PDF page/encryption checks | Presigned upload, MIME/signature checks, sandbox extraction, AV scan |

The development HS256 token service is an application-owned contract and testable fallback. Use a
real OIDC issuer and managed signing keys for production. `AUTH_REQUIRED=true` and a secret of at
least 32 characters are mandatory when `APP_ENV=production`; rotate compromised secrets and
invalidate all affected short-lived tokens.

The realtime edge enforces configured bearer/candidate-role authorization and same-origin checks.
The bundled browser does not provide production socket credentials and therefore fails closed under
required authentication. Per-interview socket binding, distributed socket quotas, and cumulative
audio accounting remain production work. Provider credentials and wire payloads remain in adapter
composition, never client configuration.

Generic provider metadata defaults to external processing and retention; application policy denies
those routes unless explicitly allowed. Local processing/no-retention declarations must be set in
deployment configuration and validated operationally, never guessed from an endpoint URL.

## Prohibited use

- No autonomous hire/reject decision or unreviewed ATS export.
- No inference or scoring of protected traits, disability, nationality, family status, religion,
  age, voice/accent, emotion, sentiment, or health.
- No use of prompt-injection, moderation, refusal, clarification, accessibility, or technical
  difficulty as an adverse hiring signal.
- No fabricated resume facts, hidden rubric changes, secret collection, or cross-candidate data.
- No production candidate data until privacy, security, fairness, accessibility, retention,
  incident response, and accountable-owner gates are signed off.

Security reports should include the affected version, reproducible evidence using synthetic data,
and whether confidentiality or tenant isolation may be impacted. Do not put candidate data or
credentials into an issue, trace, or test fixture.
