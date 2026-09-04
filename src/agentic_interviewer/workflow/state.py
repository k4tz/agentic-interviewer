from typing import Any, TypedDict


class InterviewState(TypedDict):
    interview_id: str
    tenant_id: str
    candidate_id: str
    resume_profile: dict[str, Any]
    job_profile: dict[str, Any]
    plan: dict[str, Any]
    rubric: dict[str, Any]
    status: str
    current_question_index: int
    interview_phase: str
    adaptive_candidates: list[dict[str, Any]]
    adaptive_questions: list[dict[str, Any]]
    question_history: list[dict[str, Any]]
    question_statuses: dict[str, str]
    pending_answer: str
    answer_analysis: dict[str, Any] | None
    last_response: str
    answers: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    scores: dict[str, float]
    intent_events: list[dict[str, Any]]
    redirects_by_question: dict[str, int]
    answer_attempts_by_question: dict[str, int]
    audit_events: list[dict[str, Any]]
    assessment: dict[str, Any] | None
    usage: list[dict[str, Any]]
    appeals: list[dict[str, Any]]
    export: dict[str, Any] | None
