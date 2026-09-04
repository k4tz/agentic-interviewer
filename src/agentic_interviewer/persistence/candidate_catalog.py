from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

import psycopg
from psycopg.rows import dict_row


class CatalogUnavailableError(RuntimeError):
    """The candidate intake catalog could not be reached."""


class JobNotFoundError(KeyError):
    """The requested job is not available for interviews."""


@dataclass(frozen=True)
class JobListing:
    id: str
    slug: str
    title: str
    company: str
    location: str
    description: str
    requirements: tuple[str, ...]
    company_questions: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "description": self.description,
            "requirements": list(self.requirements),
        }

    @property
    def job_details(self) -> str:
        requirements = "\n".join(f"- {item}" for item in self.requirements)
        return f"{self.description.strip()}\n\nRequirements:\n{requirements}"


@dataclass(frozen=True)
class ResumeRecord:
    id: str
    filename: str
    content_type: str
    sha256: str
    raw_bytes: bytes
    extracted_text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class IntakeRecord:
    id: str
    job_id: str
    resume_id: str
    interview_id: str
    candidate_id: str
    status: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class CandidateCatalog(Protocol):
    def list_jobs(self) -> list[JobListing]: ...

    def get_job(self, job_id: str) -> JobListing: ...

    def store_resume(self, record: ResumeRecord) -> ResumeRecord: ...

    def create_intake(self, record: IntakeRecord) -> IntakeRecord: ...


def development_jobs() -> list[JobListing]:
    return [
        JobListing(
            id="job-platform-engineer",
            slug="senior-platform-engineer",
            title="Senior Platform Engineer",
            company="Northstar Systems",
            location="Remote / India",
            description=(
                "Design and operate reliable backend platforms used by product teams. The role "
                "combines distributed systems design, API ownership, production operations, and "
                "technical leadership."
            ),
            requirements=(
                "Strong Python experience and production API design",
                "Distributed systems fundamentals, queues, caching, and failure handling",
                "PostgreSQL data modelling and query-performance experience",
                "Containers, observability, incident response, and capacity planning",
                "Clear technical communication and evidence-based trade-off decisions",
            ),
            company_questions=(
                "Tell me about a production incident you personally helped resolve and what "
                "changed afterward.",
            ),
        ),
        JobListing(
            id="job-ai-application-engineer",
            slug="ai-application-engineer",
            title="AI Application Engineer",
            company="Northstar Systems",
            location="Bengaluru / Hybrid",
            description=(
                "Build secure, observable AI product workflows with swappable model providers and "
                "well-defined application contracts."
            ),
            requirements=(
                "Python and FastAPI service development",
                "LLM application evaluation, guardrails, and structured outputs",
                "Provider-neutral model integration and cost controls",
                "Async processing, PostgreSQL, and production monitoring",
                "Practical experience shipping user-facing AI features",
            ),
            company_questions=(
                "Describe an AI system where you deliberately chose a simpler architecture over "
                "a more agentic one.",
            ),
        ),
    ]


class InMemoryCandidateCatalog:
    def __init__(self, jobs: list[JobListing] | None = None) -> None:
        self._jobs = {job.id: job for job in (jobs or development_jobs())}
        self._resumes: dict[str, ResumeRecord] = {}
        self._intakes: dict[str, IntakeRecord] = {}
        self._lock = RLock()

    def list_jobs(self) -> list[JobListing]:
        return sorted(self._jobs.values(), key=lambda job: job.title)

    def get_job(self, job_id: str) -> JobListing:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc

    def store_resume(self, record: ResumeRecord) -> ResumeRecord:
        with self._lock:
            self._resumes[record.id] = record
        return record

    def create_intake(self, record: IntakeRecord) -> IntakeRecord:
        with self._lock:
            self._intakes[record.id] = record
        return record


class PostgresCandidateCatalog:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def list_jobs(self) -> list[JobListing]:
        rows = self._fetch_all(
            """
            SELECT id, slug, title, company, location, description,
                   requirements, company_questions
            FROM jobs
            WHERE active = TRUE
            ORDER BY title
            """
        )
        return [self._job_from_row(row) for row in rows]

    def get_job(self, job_id: str) -> JobListing:
        rows = self._fetch_all(
            """
            SELECT id, slug, title, company, location, description,
                   requirements, company_questions
            FROM jobs
            WHERE id = %s AND active = TRUE
            """,
            (job_id,),
        )
        if not rows:
            raise JobNotFoundError(job_id)
        return self._job_from_row(rows[0])

    def store_resume(self, record: ResumeRecord) -> ResumeRecord:
        self._execute(
            """
            INSERT INTO resumes (
                id, filename, content_type, sha256, raw_bytes, extracted_text, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.id,
                record.filename,
                record.content_type,
                record.sha256,
                record.raw_bytes,
                record.extracted_text,
                record.created_at,
            ),
        )
        return record

    def create_intake(self, record: IntakeRecord) -> IntakeRecord:
        self._execute(
            """
            INSERT INTO interview_intakes (
                id, job_id, resume_id, interview_id, candidate_id, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.id,
                record.job_id,
                record.resume_id,
                record.interview_id,
                record.candidate_id,
                record.status,
                record.created_at,
            ),
        )
        return record

    def _fetch_all(self, query: str, parameters: tuple[object, ...] = ()) -> list[dict]:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, parameters)
                    return list(cursor.fetchall())
        except psycopg.Error as exc:
            raise CatalogUnavailableError("candidate catalog is unavailable") from exc

    def _execute(self, query: str, parameters: tuple[object, ...]) -> None:
        try:
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, parameters)
                connection.commit()
        except psycopg.Error as exc:
            raise CatalogUnavailableError("candidate catalog is unavailable") from exc

    @staticmethod
    def _job_from_row(row: dict) -> JobListing:
        return JobListing(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            description=row["description"],
            requirements=tuple(row["requirements"] or []),
            company_questions=tuple(row["company_questions"] or []),
        )
