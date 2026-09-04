# Telemetry pipeline runbook

Trigger: collector target down for five minutes, missing application series, export failures,
redaction canary failure, or unexpected label cardinality.

1. Treat a redaction failure as a privacy incident: stop content capture/export, restrict access,
   preserve metadata-only evidence, and follow the privacy escalation process.
2. For availability, check collector, Prometheus, Tempo, and Grafana health independently. A green
   collector does not prove application instrumentation is exporting.
3. Check OTLP endpoint, service name, deployment version, exporter errors, collector memory limiter,
   and backend retention/storage. Never enable verbose payload logging during diagnosis.
4. For cardinality growth, find the metric and label name, disable that instrument, and verify that
   interview/session IDs or free text were not used as labels.
5. Operational telemetry loss must not block an active interview, but it blocks production release,
   capacity changes, and claims that SLOs were met. Business audit writes follow their own durable
   failure policy.
6. After recovery, verify a synthetic metadata-only trace and counter end to end, alert delivery,
   and retention. Record the gap window and which SLO calculations are invalid.
