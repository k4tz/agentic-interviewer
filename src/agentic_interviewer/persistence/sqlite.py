from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from agentic_interviewer.persistence.contracts import ConflictError, NotFoundError
from agentic_interviewer.persistence.models import (
    AuditEntry,
    EvidenceRecord,
    ImmutableDocument,
    RetentionPolicy,
    SessionRecord,
    TranscriptRecord,
    utc_now,
)

_ZERO_HASH = "0" * 64


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteRepositories:
    """Tenant-scoped local repositories sharing one transactional SQLite connection."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = RLock()
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteRepositories:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                revision INTEGER NOT NULL,
                create_key TEXT NOT NULL,
                last_write_key TEXT NOT NULL,
                retention_class TEXT NOT NULL,
                retain_until TEXT,
                legal_hold INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, session_id),
                UNIQUE (tenant_id, create_key),
                UNIQUE (tenant_id, last_write_key)
            );
            CREATE TABLE IF NOT EXISTS plans (
                tenant_id TEXT NOT NULL, document_id TEXT NOT NULL, version TEXT NOT NULL,
                payload TEXT NOT NULL, retention_class TEXT NOT NULL, retain_until TEXT,
                legal_hold INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, document_id, version)
            );
            CREATE TABLE IF NOT EXISTS rubrics (
                tenant_id TEXT NOT NULL, document_id TEXT NOT NULL, version TEXT NOT NULL,
                payload TEXT NOT NULL, retention_class TEXT NOT NULL, retain_until TEXT,
                legal_hold INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, document_id, version)
            );
            CREATE TABLE IF NOT EXISTS session_writes (
                tenant_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                session_id TEXT NOT NULL, expected_revision INTEGER NOT NULL,
                result_revision INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, idempotency_key),
                FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, session_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS transcripts (
                tenant_id TEXT NOT NULL, transcript_id TEXT NOT NULL, session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL, payload TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                retention_class TEXT NOT NULL, retain_until TEXT,
                legal_hold INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, transcript_id),
                UNIQUE (tenant_id, idempotency_key),
                FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, session_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS evidence (
                tenant_id TEXT NOT NULL, evidence_id TEXT NOT NULL, session_id TEXT NOT NULL,
                payload TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                retention_class TEXT NOT NULL, retain_until TEXT,
                legal_hold INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, evidence_id),
                UNIQUE (tenant_id, idempotency_key),
                FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, session_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                tenant_id TEXT NOT NULL, sequence INTEGER NOT NULL, session_id TEXT,
                event_type TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL,
                request_id TEXT NOT NULL, policy_version TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                retention_class TEXT NOT NULL DEFAULT 'audit', retain_until TEXT,
                legal_hold INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, sequence),
                UNIQUE (tenant_id, idempotency_key),
                UNIQUE (tenant_id, event_hash)
            );
            """
        )

    def create_session(self, record: SessionRecord, *, idempotency_key: str) -> SessionRecord:
        self._validate_tenant(record.tenant_id)
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM sessions WHERE tenant_id=? AND create_key=?",
                (record.tenant_id, idempotency_key),
            ).fetchone()
            if existing:
                saved = self._session(existing)
                if self._session_content(saved) != self._session_content(record):
                    raise ConflictError("idempotency key was already used with different content")
                return saved
            try:
                connection.execute(
                    """INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.tenant_id,
                        record.session_id,
                        record.plan_id,
                        record.status,
                        _json(record.payload),
                        record.revision,
                        idempotency_key,
                        idempotency_key,
                        record.retention.retention_class,
                        _timestamp(record.retention.retain_until),
                        record.retention.legal_hold,
                        _timestamp(record.created_at),
                        _timestamp(record.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("session already exists") from exc
        return record

    def get_session(self, tenant_id: str, session_id: str) -> SessionRecord:
        self._validate_tenant(tenant_id)
        row = self._connection.execute(
            "SELECT * FROM sessions WHERE tenant_id=? AND session_id=?", (tenant_id, session_id)
        ).fetchone()
        if not row:
            raise NotFoundError("session not found")
        return self._session(row)

    def update_session(
        self,
        tenant_id: str,
        session_id: str,
        *,
        expected_revision: int,
        status: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> SessionRecord:
        self._validate_tenant(tenant_id)
        with self._write() as connection:
            current_row = connection.execute(
                "SELECT * FROM sessions WHERE tenant_id=? AND session_id=?",
                (tenant_id, session_id),
            ).fetchone()
            if not current_row:
                raise NotFoundError("session not found")
            current = self._session(current_row)
            duplicate = connection.execute(
                "SELECT * FROM session_writes WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if duplicate:
                same_write = (
                    duplicate["session_id"] == session_id
                    and duplicate["expected_revision"] == expected_revision
                    and duplicate["status"] == status
                    and json.loads(duplicate["payload"]) == payload
                )
                if not same_write:
                    raise ConflictError("idempotency key was already used with different content")
                return replace(
                    current,
                    status=duplicate["status"],
                    payload=json.loads(duplicate["payload"]),
                    revision=duplicate["result_revision"],
                    updated_at=_datetime(duplicate["updated_at"]),
                )
            if current.revision != expected_revision:
                raise ConflictError(
                    "stale session revision: "
                    f"expected {expected_revision}, found {current.revision}"
                )
            now = utc_now()
            connection.execute(
                """UPDATE sessions SET status=?, payload=?, revision=?, last_write_key=?,
                   updated_at=? WHERE tenant_id=? AND session_id=? AND revision=?""",
                (
                    status,
                    _json(payload),
                    current.revision + 1,
                    idempotency_key,
                    _timestamp(now),
                    tenant_id,
                    session_id,
                    expected_revision,
                ),
            )
            connection.execute(
                """INSERT INTO session_writes
                   (tenant_id, idempotency_key, session_id, expected_revision,
                    result_revision, status, payload, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    idempotency_key,
                    session_id,
                    expected_revision,
                    current.revision + 1,
                    status,
                    _json(payload),
                    _timestamp(now),
                ),
            )
            return replace(
                current,
                status=status,
                payload=payload,
                revision=current.revision + 1,
                updated_at=now,
            )

    def put_plan(self, record: ImmutableDocument) -> ImmutableDocument:
        return self._put_document("plans", record)

    def get_plan(self, tenant_id: str, document_id: str, version: str) -> ImmutableDocument:
        return self._get_document("plans", tenant_id, document_id, version)

    def put_rubric(self, record: ImmutableDocument) -> ImmutableDocument:
        return self._put_document("rubrics", record)

    def get_rubric(self, tenant_id: str, document_id: str, version: str) -> ImmutableDocument:
        return self._get_document("rubrics", tenant_id, document_id, version)

    def append_transcript(self, record: TranscriptRecord) -> TranscriptRecord:
        values = (
            record.tenant_id,
            record.transcript_id,
            record.session_id,
            record.turn_id,
            _json(record.payload),
            record.idempotency_key,
            record.retention.retention_class,
            _timestamp(record.retention.retain_until),
            record.retention.legal_hold,
            _timestamp(record.created_at),
        )
        self._append_idempotent("transcripts", "transcript_id", record, values)
        return self._find_transcript(record.tenant_id, record.idempotency_key)

    def list_transcripts(self, tenant_id: str, session_id: str) -> list[TranscriptRecord]:
        self._validate_tenant(tenant_id)
        rows = self._connection.execute(
            """SELECT * FROM transcripts WHERE tenant_id=? AND session_id=?
               ORDER BY created_at, transcript_id""",
            (tenant_id, session_id),
        ).fetchall()
        return [self._transcript(row) for row in rows]

    def append_evidence(self, record: EvidenceRecord) -> EvidenceRecord:
        values = (
            record.tenant_id,
            record.evidence_id,
            record.session_id,
            _json(record.payload),
            record.idempotency_key,
            record.retention.retention_class,
            _timestamp(record.retention.retain_until),
            record.retention.legal_hold,
            _timestamp(record.created_at),
        )
        self._append_idempotent("evidence", "evidence_id", record, values)
        return self._find_evidence(record.tenant_id, record.idempotency_key)

    def list_evidence(self, tenant_id: str, session_id: str) -> list[EvidenceRecord]:
        self._validate_tenant(tenant_id)
        rows = self._connection.execute(
            """SELECT * FROM evidence WHERE tenant_id=? AND session_id=?
               ORDER BY created_at, evidence_id""",
            (tenant_id, session_id),
        ).fetchall()
        return [self._evidence(row) for row in rows]

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
    ) -> AuditEntry:
        self._validate_tenant(tenant_id)
        with self._write() as connection:
            duplicate = connection.execute(
                "SELECT * FROM audit_events WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if duplicate:
                entry = self._audit(duplicate)
                expected = (event_type, actor, detail, request_id, policy_version, session_id)
                actual = (
                    entry.event_type,
                    entry.actor,
                    entry.detail,
                    entry.request_id,
                    entry.policy_version,
                    entry.session_id,
                )
                if actual != expected:
                    raise ConflictError("idempotency key was already used with different content")
                return entry
            previous = connection.execute(
                """SELECT sequence, event_hash FROM audit_events WHERE tenant_id=?
                   ORDER BY sequence DESC LIMIT 1""",
                (tenant_id,),
            ).fetchone()
            sequence = int(previous["sequence"]) + 1 if previous else 1
            previous_hash = str(previous["event_hash"]) if previous else _ZERO_HASH
            created_at = utc_now()
            event_hash = self._audit_hash(
                tenant_id,
                sequence,
                session_id,
                event_type,
                actor,
                detail,
                request_id,
                policy_version,
                previous_hash,
                created_at,
            )
            connection.execute(
                """INSERT INTO audit_events
                   (tenant_id, sequence, session_id, event_type, actor, detail, request_id,
                    policy_version, idempotency_key, previous_hash, event_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    sequence,
                    session_id,
                    event_type,
                    actor,
                    _json(detail),
                    request_id,
                    policy_version,
                    idempotency_key,
                    previous_hash,
                    event_hash,
                    _timestamp(created_at),
                ),
            )
        return AuditEntry(
            tenant_id=tenant_id,
            sequence=sequence,
            session_id=session_id,
            event_type=event_type,
            actor=actor,
            detail=detail,
            request_id=request_id,
            policy_version=policy_version,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=created_at,
        )

    def list_audit(self, tenant_id: str) -> list[AuditEntry]:
        self._validate_tenant(tenant_id)
        rows = self._connection.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence", (tenant_id,)
        ).fetchall()
        return [self._audit(row) for row in rows]

    def verify_audit_chain(self, tenant_id: str) -> bool:
        previous_hash = _ZERO_HASH
        expected_sequence = 1
        for entry in self.list_audit(tenant_id):
            calculated = self._audit_hash(
                entry.tenant_id,
                entry.sequence,
                entry.session_id,
                entry.event_type,
                entry.actor,
                entry.detail,
                entry.request_id,
                entry.policy_version,
                entry.previous_hash,
                entry.created_at,
            )
            if (
                entry.sequence != expected_sequence
                or entry.previous_hash != previous_hash
                or entry.event_hash != calculated
            ):
                return False
            expected_sequence += 1
            previous_hash = entry.event_hash
        return True

    def set_legal_hold(
        self, tenant_id: str, resource_type: str, resource_id: str, *, enabled: bool
    ) -> None:
        self._validate_tenant(tenant_id)
        table, id_column = self._resource_table(resource_type)
        with self._write() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET legal_hold=? WHERE tenant_id=? AND {id_column}=?",  # noqa: S608
                (enabled, tenant_id, resource_id),
            )
            if cursor.rowcount == 0:
                raise NotFoundError(f"{resource_type} not found")

    def purge_expired(self, *, before: datetime) -> dict[str, int]:
        cutoff = _timestamp(before)
        counts: dict[str, int] = {}
        with self._write() as connection:
            for table in ("transcripts", "evidence", "plans", "rubrics"):
                cursor = connection.execute(
                    f"DELETE FROM {table} "  # noqa: S608
                    "WHERE legal_hold=0 AND retain_until IS NOT NULL AND retain_until<=?",
                    (cutoff,),
                )
                counts[table] = cursor.rowcount
            cursor = connection.execute(
                """DELETE FROM sessions
                   WHERE legal_hold=0 AND retain_until IS NOT NULL AND retain_until<=?
                     AND NOT EXISTS (
                         SELECT 1 FROM transcripts t
                         WHERE t.tenant_id=sessions.tenant_id
                           AND t.session_id=sessions.session_id AND t.legal_hold=1
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM evidence e
                         WHERE e.tenant_id=sessions.tenant_id
                           AND e.session_id=sessions.session_id AND e.legal_hold=1
                     )""",
                (cutoff,),
            )
            counts["sessions"] = cursor.rowcount
        return counts

    def delete_session(self, tenant_id: str, session_id: str) -> None:
        self._validate_tenant(tenant_id)
        with self._write() as connection:
            row = connection.execute(
                "SELECT legal_hold FROM sessions WHERE tenant_id=? AND session_id=?",
                (tenant_id, session_id),
            ).fetchone()
            if not row:
                raise NotFoundError("session not found")
            held_children = connection.execute(
                """SELECT 1 FROM transcripts WHERE tenant_id=? AND session_id=? AND legal_hold=1
                   UNION ALL
                   SELECT 1 FROM evidence WHERE tenant_id=? AND session_id=? AND legal_hold=1
                   LIMIT 1""",
                (tenant_id, session_id, tenant_id, session_id),
            ).fetchone()
            if row["legal_hold"] or held_children:
                raise ConflictError("session or child resource is under legal hold")
            connection.execute(
                "DELETE FROM sessions WHERE tenant_id=? AND session_id=?",
                (tenant_id, session_id),
            )

    def _put_document(self, table: str, record: ImmutableDocument) -> ImmutableDocument:
        self._validate_tenant(record.tenant_id)
        with self._write() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE tenant_id=? AND document_id=? AND version=?",  # noqa: S608
                (record.tenant_id, record.document_id, record.version),
            ).fetchone()
            if row:
                existing = self._document(row)
                if existing.payload != record.payload:
                    raise ConflictError("immutable document version already has different content")
                return existing
            connection.execute(
                f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
                (
                    record.tenant_id,
                    record.document_id,
                    record.version,
                    _json(record.payload),
                    record.retention.retention_class,
                    _timestamp(record.retention.retain_until),
                    record.retention.legal_hold,
                    _timestamp(record.created_at),
                ),
            )
        return record

    def _get_document(
        self, table: str, tenant_id: str, document_id: str, version: str
    ) -> ImmutableDocument:
        self._validate_tenant(tenant_id)
        row = self._connection.execute(
            f"SELECT * FROM {table} WHERE tenant_id=? AND document_id=? AND version=?",  # noqa: S608
            (tenant_id, document_id, version),
        ).fetchone()
        if not row:
            raise NotFoundError("document not found")
        return self._document(row)

    def _append_idempotent(
        self, table: str, id_column: str, record: TranscriptRecord | EvidenceRecord, values: tuple
    ) -> None:
        self._validate_tenant(record.tenant_id)
        with self._write() as connection:
            duplicate = connection.execute(
                f"SELECT * FROM {table} WHERE tenant_id=? AND idempotency_key=?",  # noqa: S608
                (record.tenant_id, record.idempotency_key),
            ).fetchone()
            if duplicate:
                if isinstance(record, TranscriptRecord):
                    existing_content = (
                        duplicate["transcript_id"],
                        duplicate["session_id"],
                        duplicate["turn_id"],
                        json.loads(duplicate["payload"]),
                    )
                    requested_content = (
                        record.transcript_id,
                        record.session_id,
                        record.turn_id,
                        record.payload,
                    )
                else:
                    existing_content = (
                        duplicate["evidence_id"],
                        duplicate["session_id"],
                        json.loads(duplicate["payload"]),
                    )
                    requested_content = (
                        record.evidence_id,
                        record.session_id,
                        record.payload,
                    )
                if existing_content != requested_content:
                    raise ConflictError("idempotency key was already used with different content")
                return
            placeholders = ", ".join("?" for _ in values)
            try:
                connection.execute(
                    f"INSERT INTO {table} VALUES ({placeholders})",  # noqa: S608
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(f"cannot append {table.rstrip('s')}") from exc

    def _find_transcript(self, tenant_id: str, key: str) -> TranscriptRecord:
        row = self._connection.execute(
            "SELECT * FROM transcripts WHERE tenant_id=? AND idempotency_key=?", (tenant_id, key)
        ).fetchone()
        return self._transcript(row)

    def _find_evidence(self, tenant_id: str, key: str) -> EvidenceRecord:
        row = self._connection.execute(
            "SELECT * FROM evidence WHERE tenant_id=? AND idempotency_key=?", (tenant_id, key)
        ).fetchone()
        return self._evidence(row)

    @staticmethod
    def _validate_tenant(tenant_id: str) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")

    @staticmethod
    def _session_content(record: SessionRecord) -> tuple:
        return (record.session_id, record.plan_id, record.status, record.payload, record.revision)

    @staticmethod
    def _retention(row: sqlite3.Row) -> RetentionPolicy:
        return RetentionPolicy(
            retention_class=row["retention_class"],
            retain_until=_datetime(row["retain_until"]),
            legal_hold=bool(row["legal_hold"]),
        )

    def _session(self, row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            plan_id=row["plan_id"],
            status=row["status"],
            payload=json.loads(row["payload"]),
            revision=row["revision"],
            retention=self._retention(row),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    def _document(self, row: sqlite3.Row) -> ImmutableDocument:
        return ImmutableDocument(
            tenant_id=row["tenant_id"],
            document_id=row["document_id"],
            version=row["version"],
            payload=json.loads(row["payload"]),
            retention=self._retention(row),
            created_at=_datetime(row["created_at"]),
        )

    def _transcript(self, row: sqlite3.Row) -> TranscriptRecord:
        return TranscriptRecord(
            tenant_id=row["tenant_id"],
            transcript_id=row["transcript_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            payload=json.loads(row["payload"]),
            idempotency_key=row["idempotency_key"],
            retention=self._retention(row),
            created_at=_datetime(row["created_at"]),
        )

    def _evidence(self, row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            tenant_id=row["tenant_id"],
            evidence_id=row["evidence_id"],
            session_id=row["session_id"],
            payload=json.loads(row["payload"]),
            idempotency_key=row["idempotency_key"],
            retention=self._retention(row),
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _audit_hash(
        tenant_id: str,
        sequence: int,
        session_id: str | None,
        event_type: str,
        actor: str,
        detail: dict[str, Any],
        request_id: str,
        policy_version: str,
        previous_hash: str,
        created_at: datetime,
    ) -> str:
        content = _json(
            {
                "actor": actor,
                "created_at": _timestamp(created_at),
                "detail": detail,
                "event_type": event_type,
                "policy_version": policy_version,
                "previous_hash": previous_hash,
                "request_id": request_id,
                "sequence": sequence,
                "session_id": session_id,
                "tenant_id": tenant_id,
            }
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _audit(self, row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            tenant_id=row["tenant_id"],
            sequence=row["sequence"],
            session_id=row["session_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            detail=json.loads(row["detail"]),
            request_id=row["request_id"],
            policy_version=row["policy_version"],
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _resource_table(resource_type: str) -> tuple[str, str]:
        allowed = {
            "session": ("sessions", "session_id"),
            "plan": ("plans", "document_id"),
            "rubric": ("rubrics", "document_id"),
            "transcript": ("transcripts", "transcript_id"),
            "evidence": ("evidence", "evidence_id"),
        }
        try:
            return allowed[resource_type]
        except KeyError as exc:
            raise ValueError(f"unsupported resource type: {resource_type}") from exc
