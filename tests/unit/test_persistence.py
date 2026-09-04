from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic_interviewer.persistence import (
    EvidenceRecord,
    ImmutableDocument,
    RetentionPolicy,
    SessionRecord,
    SQLiteRepositories,
    TranscriptRecord,
)
from agentic_interviewer.persistence.contracts import ConflictError, NotFoundError
from agentic_interviewer.persistence.postgres_checkpoints import (
    PostgresCheckpointSettings,
    compile_with_postgres_checkpoints,
)


@pytest.fixture
def repositories():
    with SQLiteRepositories() as repository:
        yield repository


def session(tenant_id: str = "tenant-a", **changes) -> SessionRecord:
    values = {
        "tenant_id": tenant_id,
        "session_id": "session-1",
        "plan_id": "plan-1",
        "status": "created",
        "payload": {"candidate_id": "candidate-1"},
    }
    values.update(changes)
    return SessionRecord(**values)


def test_all_reads_are_tenant_scoped(repositories: SQLiteRepositories):
    repositories.create_session(session(), idempotency_key="create-a")
    repositories.create_session(session("tenant-b"), idempotency_key="create-b")

    assert repositories.get_session("tenant-a", "session-1").tenant_id == "tenant-a"
    assert repositories.get_session("tenant-b", "session-1").tenant_id == "tenant-b"
    with pytest.raises(NotFoundError):
        repositories.get_session("tenant-c", "session-1")


def test_session_writes_are_optimistic_and_idempotent(repositories: SQLiteRepositories):
    created = repositories.create_session(session(), idempotency_key="create")
    assert repositories.create_session(session(), idempotency_key="create") == created

    updated = repositories.update_session(
        "tenant-a",
        "session-1",
        expected_revision=1,
        status="in_progress",
        payload={"candidate_id": "candidate-1", "question": 1},
        idempotency_key="start",
    )
    replay = repositories.update_session(
        "tenant-a",
        "session-1",
        expected_revision=1,
        status="in_progress",
        payload={"candidate_id": "candidate-1", "question": 1},
        idempotency_key="start",
    )
    assert replay == updated
    assert updated.revision == 2

    repositories.update_session(
        "tenant-a",
        "session-1",
        expected_revision=2,
        status="completed",
        payload={"candidate_id": "candidate-1", "question": 2},
        idempotency_key="complete",
    )
    old_replay = repositories.update_session(
        "tenant-a",
        "session-1",
        expected_revision=1,
        status="in_progress",
        payload={"candidate_id": "candidate-1", "question": 1},
        idempotency_key="start",
    )
    assert old_replay == updated

    with pytest.raises(ConflictError, match="stale"):
        repositories.update_session(
            "tenant-a",
            "session-1",
            expected_revision=1,
            status="paused",
            payload={},
            idempotency_key="pause",
        )


def test_plan_and_rubric_versions_are_immutable_and_tenant_scoped(
    repositories: SQLiteRepositories,
):
    plan = ImmutableDocument("tenant-a", "plan-1", "1", {"questions": ["q1"]})
    rubric = ImmutableDocument("tenant-a", "rubric-1", "1", {"anchors": [1, 2]})
    assert repositories.put_plan(plan) == plan
    assert repositories.put_plan(plan) == plan
    assert repositories.put_rubric(rubric) == rubric

    with pytest.raises(ConflictError, match="immutable"):
        repositories.put_plan(
            ImmutableDocument("tenant-a", "plan-1", "1", {"questions": ["changed"]})
        )
    with pytest.raises(NotFoundError):
        repositories.get_rubric("tenant-b", "rubric-1", "1")


def test_transcript_and_evidence_appends_are_idempotent(repositories: SQLiteRepositories):
    repositories.create_session(session(), idempotency_key="create")
    transcript = TranscriptRecord(
        "tenant-a", "transcript-1", "session-1", "turn-1", {"text": "hello"}, "stt-1"
    )
    evidence = EvidenceRecord(
        "tenant-a", "evidence-1", "session-1", {"claim": "built it"}, "evidence-1"
    )

    repositories.append_transcript(transcript)
    repositories.append_transcript(transcript)
    repositories.append_evidence(evidence)
    repositories.append_evidence(evidence)

    assert repositories.list_transcripts("tenant-a", "session-1") == [transcript]
    assert repositories.list_evidence("tenant-a", "session-1") == [evidence]
    assert repositories.list_transcripts("tenant-b", "session-1") == []
    with pytest.raises(ConflictError, match="different content"):
        repositories.append_transcript(
            TranscriptRecord(
                "tenant-a",
                "transcript-1",
                "session-1",
                "turn-1",
                {"text": "changed"},
                "stt-1",
            )
        )


def test_retention_purge_respects_legal_hold_and_hard_delete_cascades(
    repositories: SQLiteRepositories,
):
    expired = datetime.now(UTC) - timedelta(days=1)
    retention = RetentionPolicy(retain_until=expired)
    repositories.create_session(
        session(retention=retention),
        idempotency_key="create",
    )
    repositories.append_transcript(
        TranscriptRecord(
            "tenant-a",
            "transcript-1",
            "session-1",
            "turn-1",
            {"text": "held"},
            "stt-1",
            retention,
        )
    )
    repositories.set_legal_hold("tenant-a", "transcript", "transcript-1", enabled=True)

    counts = repositories.purge_expired(before=datetime.now(UTC))
    assert counts["sessions"] == 0
    assert repositories.list_transcripts("tenant-a", "session-1")[0].payload["text"] == "held"
    with pytest.raises(ConflictError, match="legal hold"):
        repositories.delete_session("tenant-a", "session-1")

    repositories.set_legal_hold("tenant-a", "transcript", "transcript-1", enabled=False)
    repositories.delete_session("tenant-a", "session-1")
    with pytest.raises(NotFoundError):
        repositories.get_session("tenant-a", "session-1")
    assert repositories.list_transcripts("tenant-a", "session-1") == []


def test_audit_chain_is_tenant_local_idempotent_and_tamper_evident(
    repositories: SQLiteRepositories,
):
    first = repositories.append_audit(
        "tenant-a",
        event_type="interview_created",
        actor="system",
        detail={"source": "api"},
        request_id="request-1",
        policy_version="1",
        idempotency_key="audit-1",
        session_id="session-1",
    )
    replay = repositories.append_audit(
        "tenant-a",
        event_type="interview_created",
        actor="system",
        detail={"source": "api"},
        request_id="request-1",
        policy_version="1",
        idempotency_key="audit-1",
        session_id="session-1",
    )
    second = repositories.append_audit(
        "tenant-a",
        event_type="interview_started",
        actor="candidate",
        detail={},
        request_id="request-2",
        policy_version="1",
        idempotency_key="audit-2",
        session_id="session-1",
    )

    assert replay == first
    assert second.previous_hash == first.event_hash
    assert repositories.verify_audit_chain("tenant-a") is True
    assert repositories.list_audit("tenant-b") == []

    repositories._connection.execute(  # noqa: SLF001 - deliberate corruption test
        "UPDATE audit_events SET detail=? WHERE tenant_id=? AND sequence=?",
        ('{"source":"tampered"}', "tenant-a", 1),
    )
    assert repositories.verify_audit_chain("tenant-a") is False


def test_postgres_checkpointer_dependency_is_loaded_only_when_entered():
    settings = PostgresCheckpointSettings("postgresql://unused", require_strict_msgpack=False)
    context = compile_with_postgres_checkpoints(object(), settings)

    with pytest.raises(RuntimeError, match="langgraph-checkpoint-postgres"):
        with context:
            pass


def test_postgres_checkpointer_requires_safe_deserialization_by_default(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_STRICT_MSGPACK", raising=False)
    context = compile_with_postgres_checkpoints(
        object(), PostgresCheckpointSettings("postgresql://unused")
    )

    with pytest.raises(RuntimeError, match="LANGGRAPH_STRICT_MSGPACK"):
        with context:
            pass
