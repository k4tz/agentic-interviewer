from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    retention_class: str = "standard"
    retain_until: datetime | None = None
    legal_hold: bool = False


@dataclass(frozen=True, slots=True)
class SessionRecord:
    tenant_id: str
    session_id: str
    plan_id: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    revision: int = 1
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ImmutableDocument:
    tenant_id: str
    document_id: str
    version: str
    payload: dict[str, Any]
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TranscriptRecord:
    tenant_id: str
    transcript_id: str
    session_id: str
    turn_id: str
    payload: dict[str, Any]
    idempotency_key: str
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    tenant_id: str
    evidence_id: str
    session_id: str
    payload: dict[str, Any]
    idempotency_key: str
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    tenant_id: str
    sequence: int
    event_type: str
    actor: str
    detail: dict[str, Any]
    request_id: str
    policy_version: str
    previous_hash: str
    event_hash: str
    created_at: datetime
    session_id: str | None = None
