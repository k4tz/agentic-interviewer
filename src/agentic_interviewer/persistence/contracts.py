from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from agentic_interviewer.persistence.models import (
    AuditEntry,
    EvidenceRecord,
    ImmutableDocument,
    SessionRecord,
    TranscriptRecord,
)


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


class SessionRepository(Protocol):
    def create_session(self, record: SessionRecord, *, idempotency_key: str) -> SessionRecord: ...

    def get_session(self, tenant_id: str, session_id: str) -> SessionRecord: ...

    def update_session(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        status: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> SessionRecord: ...


class PlanRepository(Protocol):
    def put_plan(self, record: ImmutableDocument) -> ImmutableDocument: ...

    def get_plan(self, tenant_id: str, document_id: str, version: str) -> ImmutableDocument: ...


class RubricRepository(Protocol):
    def put_rubric(self, record: ImmutableDocument) -> ImmutableDocument: ...

    def get_rubric(self, tenant_id: str, document_id: str, version: str) -> ImmutableDocument: ...


class TranscriptRepository(Protocol):
    def append_transcript(self, record: TranscriptRecord) -> TranscriptRecord: ...

    def list_transcripts(self, tenant_id: str, session_id: str) -> list[TranscriptRecord]: ...


class EvidenceRepository(Protocol):
    def append_evidence(self, record: EvidenceRecord) -> EvidenceRecord: ...

    def list_evidence(self, tenant_id: str, session_id: str) -> list[EvidenceRecord]: ...


class AuditRepository(Protocol):
    def append_audit(
        self,
        tenant_id: str,
        *,
        event_type: str,
        actor: str,
        detail: dict[str, Any],
        request_id: str,
        policy_version: str,
        idempotency_key: str,
        session_id: str | None = None,
    ) -> AuditEntry: ...

    def list_audit(self, tenant_id: str) -> list[AuditEntry]: ...

    def verify_audit_chain(self, tenant_id: str) -> bool: ...


class RetentionRepository(Protocol):
    def set_legal_hold(
        self, tenant_id: str, resource_type: str, resource_id: str, *, enabled: bool
    ) -> None: ...

    def purge_expired(self, *, before: datetime) -> dict[str, int]: ...

    def delete_session(self, tenant_id: str, session_id: str) -> None: ...


class InterviewRepository(
    SessionRepository,
    PlanRepository,
    RubricRepository,
    AuditRepository,
    RetentionRepository,
    Protocol,
):
    """Composite application repository without coupling services to a storage engine."""
