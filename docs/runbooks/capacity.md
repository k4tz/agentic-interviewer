# Capacity and admission runbook

Trigger: sustained admission rejection rate above 5%, queue age above the live deadline, or a
provider pool at its declared concurrency ceiling.

1. Confirm scope by deployment, tenant tag, priority, capability, and provider. Never inspect
   candidate content.
2. Check whether rejection is tenant-local, global, or caused by provider saturation. Compare
   active work, queue age, provider p95 latency, timeout rate, and fallback rate.
3. Preserve live-session priority. Stop admitting new voice sessions when deadlines cannot be met;
   offer text or rescheduling according to product policy.
4. If one tenant is responsible, retain the tenant quota and contact its owner. Do not increase a
   global limit merely to bypass a tenant guardrail.
5. Scale only within tested capacity and provider quota. If no tested headroom exists, keep
   admission closed and communicate the incident.
6. Recover gradually, watching queue age and p95/p99 latency. Record start/end, affected sessions,
   changes, and rollback decisions without candidate content.

Do not claim the incident resolved until new admissions remain within latency targets for 15
minutes. A capacity change requires a repeatable load artifact and owner approval.
