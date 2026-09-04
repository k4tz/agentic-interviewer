# Session budget runbook

Trigger: repeated 80% warnings, a hard token/audio/TTS/cost limit, or an unexpected cost slope.

1. Identify the dimension and affected provider/model/profile using tag-safe telemetry.
2. Confirm normalized usage source (`provider`, `measured`, or `estimated`) and rule out duplicate
   accounting. Do not zero or mutate an in-flight ledger.
3. At a hard limit, follow the declared safe-close or pause path. Overrides require an authorized
   actor, reason, new immutable ceiling, and audit event.
4. For a fleet-level spike, disable new admission to the affected route and select only fallbacks
   that still satisfy privacy, region, quality, structured-output, and latency constraints.
5. Compare prompt/context growth, retry amplification, audio silence, output caps, and cache hit
   rate. Correct the cause rather than silently raising limits.
6. Reconcile estimated cost against provider billing offline and record the variance and usage
   source. Candidate content must not be copied into the incident record.

Close only after the budget decision is audited, duplicate charging is excluded, and the cost
owner accepts the reconciliation.
