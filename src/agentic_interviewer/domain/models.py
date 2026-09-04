from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InterviewStatus(StrEnum):
    CREATED = "created"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PAUSED = "paused"
    WITHDRAWN = "withdrawn"
    APPROVED = "approved"


class InterviewPhase(StrEnum):
    BASE = "base"
    ADAPTIVE = "adaptive"
    COMPLETE = "complete"


class QuestionPhase(StrEnum):
    BASE = "base"
    ADAPTIVE = "adaptive"


class QuestionSource(StrEnum):
    LEGACY = "legacy"
    INTRODUCTION = "introduction"
    RESUME = "resume"
    JOB = "job"
    COMPANY = "company"
    ADAPTIVE = "adaptive"


class QuestionStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    ASKED = "asked"
    ANSWERED = "answered"
    COVERED_ELSEWHERE = "covered_elsewhere"
    SKIPPED = "skipped"
    OBSOLETE = "obsolete"
    FAILED = "failed"


class CompanyQuestionPolicy(StrEnum):
    REPLACE_WITHIN_BASE_BUDGET = "replace_within_budget"
    APPEND_WITHIN_TOTAL_BUDGET = "extend_budget"


class CandidateIntent(StrEnum):
    ANSWER = "answer"
    INSUFFICIENT_ANSWER = "insufficient_answer"
    REFUSAL_OR_SKIP = "refusal_or_skip"
    UNINTELLIGIBLE = "unintelligible"
    CLARIFICATION_REQUEST = "clarification_request"
    REPEAT_REQUEST = "repeat_request"
    CANDIDATE_QUESTION = "candidate_question"
    ANSWER_SEEKING = "answer_seeking"
    COERCION_OR_MANIPULATION = "coercion_or_manipulation"
    DERAILMENT = "derailment"
    PROMPT_INJECTION = "prompt_injection"
    ABUSIVE_OR_UNSAFE = "abusive_or_unsafe"
    STOP_OR_WITHDRAW = "stop_or_withdraw"
    TECHNICAL_PROBLEM = "technical_problem"
    UNCERTAIN = "uncertain"


class AnswerDisposition(StrEnum):
    ACCEPT = "accept"
    FOLLOW_UP = "follow_up"
    INSUFFICIENT = "insufficient"
    GIBBERISH = "gibberish"
    SKIP = "skip"


class AnswerAnalysis(BaseModel):
    disposition: AnswerDisposition
    confidence: float = Field(ge=0, le=1)
    feedback: str = Field(default="", max_length=500)
    follow_up_question: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_follow_up(self) -> AnswerAnalysis:
        if self.disposition is AnswerDisposition.FOLLOW_UP and not (
            self.follow_up_question and self.follow_up_question.strip()
        ):
            raise ValueError("follow_up disposition requires a question")
        if self.disposition is not AnswerDisposition.FOLLOW_UP:
            self.follow_up_question = None
        return self


class TextSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def validate_range(self) -> TextSpan:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class ExecutionControls(BaseModel):
    deadline_ms: int = Field(default=10_000, gt=0)
    max_queue_ms: int = Field(default=1_000, ge=0)
    max_retries: int = Field(default=1, ge=0, le=3)
    priority: Literal["live", "normal", "batch"] = "normal"
    idempotency_key: str = Field(min_length=1)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_audio_seconds: float | None = Field(default=None, gt=0)
    max_output_characters: int | None = Field(default=None, gt=0)
    require_structured_output: bool = False
    require_verbatim_output: bool = False
    require_live_audio_input: bool = False
    require_partial_transcripts: bool = False
    require_server_vad: bool = False
    require_manual_commit: bool = False
    require_streaming_audio_output: bool = False
    require_barge_in: bool = False
    require_word_timestamps: bool = False
    require_segment_timestamps: bool = False
    allow_fallback: bool = True
    allow_external_processing: bool = False
    allowed_regions: set[str] = Field(default_factory=set)
    retain_provider_data: bool = False


class ProviderCapabilities(BaseModel):
    provider: str
    model: str
    region: str = "local"
    external_processing: bool = False
    live_audio_input: bool = False
    partial_transcripts: bool = False
    server_vad: bool = False
    manual_commit: bool = False
    word_timestamps: bool = False
    segment_timestamps: bool = False
    structured_output: bool = False
    streaming_audio_output: bool = False
    barge_in: bool = False
    verbatim_tts: bool = False
    supported_languages: set[str] = Field(default_factory=lambda: {"en"})
    supported_input_audio_formats: set[str] = Field(default_factory=lambda: {"wav"})
    supported_output_audio_formats: set[str] = Field(default_factory=lambda: {"wav"})

    @property
    def streaming_stt(self) -> bool:
        """Compatibility view; realtime input and partial results remain distinct."""
        return self.live_audio_input and self.partial_transcripts

    @property
    def streaming_tts(self) -> bool:
        """Compatibility view for the former capability name."""
        return self.streaming_audio_output

    @property
    def supported_audio_formats(self) -> set[str]:
        """Compatibility view for adapters that use the same formats both ways."""
        return self.supported_input_audio_formats | self.supported_output_audio_formats


class NormalizedUsage(BaseModel):
    provider: str
    model: str
    capability: Literal["reasoning", "transcription", "synthesis"]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    input_audio_seconds: float | None = Field(default=None, ge=0)
    output_audio_seconds: float | None = Field(default=None, ge=0)
    synthesized_characters: int | None = Field(default=None, ge=0)
    queue_ms: int = Field(default=0, ge=0)
    time_to_first_result_ms: int | None = Field(default=None, ge=0)
    total_latency_ms: int = Field(default=0, ge=0)
    estimated_cost_usd: Decimal = Decimal("0")
    usage_source: Literal["provider", "measured", "estimated"] = "measured"


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class Transcript(BaseModel):
    text: str
    language: str | None = "en"
    confidence: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int = Field(default=0, ge=0)
    is_final: bool = True


class TranscriptionRequest(BaseModel):
    audio: bytes
    audio_format: str = "wav"
    language: str = "en"
    controls: ExecutionControls


class TranscriptionResult(BaseModel):
    transcript: Transcript
    usage: NormalizedUsage


class ReasoningRequest(BaseModel):
    task: Literal[
        "prepare_question_bank",
        "render_question",
        "analyze_answer",
        "extract_evidence",
        "generate_adaptive_candidates",
        "score",
        "classify_intent",
    ]
    prompt: str
    context: dict[str, Any] = Field(default_factory=dict)
    controls: ExecutionControls


class ReasoningResult(BaseModel):
    value: dict[str, Any]
    usage: NormalizedUsage


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    speed: float = Field(default=1.0, ge=0.25, le=4)
    output_format: str = "wav"
    controls: ExecutionControls


class AudioEvent(BaseModel):
    audio: bytes
    is_final: bool = True
    usage: NormalizedUsage | None = None


class IntentDecision(BaseModel):
    primary_intent: CandidateIntent
    confidence: float = Field(ge=0, le=1)
    matched_rule_ids: list[str] = Field(default_factory=list)
    evidence_spans: list[TextSpan] = Field(default_factory=list)
    proposed_action: Literal["allow", "clarify", "redirect", "warn", "pause", "stop"]


class Competency(BaseModel):
    id: str
    name: str
    description: str
    weight: float = Field(default=1, gt=0)


class SourceClaim(BaseModel):
    id: str
    kind: Literal["experience", "skill", "education", "requirement", "other"]
    value: str
    source_document: str
    span: TextSpan


class ResumeProfile(BaseModel):
    id: str
    candidate_id: str
    claims: list[SourceClaim] = Field(default_factory=list)
    redacted_fields: set[str] = Field(default_factory=set)
    extraction_version: str = "deterministic-v1"


class JobProfile(BaseModel):
    id: str
    title: str
    requirements: list[SourceClaim] = Field(default_factory=list)
    prohibited_topics: list[str] = Field(default_factory=list)
    extraction_version: str = "deterministic-v1"


class ScoreAnchor(BaseModel):
    score: int = Field(ge=0, le=4)
    description: str


class RubricCompetency(BaseModel):
    competency_id: str
    weight: float = Field(gt=0)
    anchors: list[ScoreAnchor] = Field(min_length=1)


class Rubric(BaseModel):
    id: str
    version: str
    competencies: list[RubricCompetency] = Field(min_length=1)
    prohibited_criteria: list[str] = Field(default_factory=list)
    approved: bool = False


class InterviewFocus(BaseModel):
    problem_solving: float = Field(default=0.25, ge=0, le=1)
    system_design: float = Field(default=0.25, ge=0, le=1)
    technical_depth: float = Field(default=0.25, ge=0, le=1)
    behavioral: float = Field(default=0.25, ge=0, le=1)

    @model_validator(mode="after")
    def require_nonzero_focus(self) -> InterviewFocus:
        if not any(
            (
                self.problem_solving,
                self.system_design,
                self.technical_depth,
                self.behavioral,
            )
        ):
            raise ValueError("at least one interview focus weight must be positive")
        return self


class InterviewPlanningControls(BaseModel):
    """Auditable base-question allocation and conversational follow-up limits.

    A plan with ``controls=None`` is a legacy linear plan and never grows
    adaptive follow-ups.
    """

    introduction_questions: int = Field(default=1, ge=0, le=3)
    resume_questions: int = Field(default=5, ge=0, le=20)
    job_questions: int = Field(default=5, ge=0, le=20)
    company_question_policy: CompanyQuestionPolicy = (
        CompanyQuestionPolicy.REPLACE_WITHIN_BASE_BUDGET
    )
    max_company_questions: int = Field(default=5, ge=0, le=20)
    max_adaptive_questions: int = Field(default=6, ge=0, le=20)
    max_adaptive_per_base_question: int = Field(default=2, ge=0, le=5)
    max_total_questions: int = Field(default=20, ge=1, le=50)
    focus: InterviewFocus = Field(default_factory=InterviewFocus)

    @property
    def base_question_budget(self) -> int:
        return self.introduction_questions + self.resume_questions + self.job_questions

    @model_validator(mode="after")
    def validate_question_budgets(self) -> InterviewPlanningControls:
        if self.max_total_questions < self.base_question_budget:
            raise ValueError("max_total_questions is smaller than the configured base allocation")
        if (
            self.company_question_policy is CompanyQuestionPolicy.REPLACE_WITHIN_BASE_BUDGET
            and self.max_company_questions > self.resume_questions + self.job_questions
        ):
            raise ValueError("company replacement budget exceeds resume and job allocation")
        return self


class Question(BaseModel):
    id: str
    competency_id: str
    text: str
    phase: QuestionPhase = QuestionPhase.BASE
    source: QuestionSource = QuestionSource.LEGACY
    status: QuestionStatus = QuestionStatus.PLANNED
    required: bool = False
    source_question_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_question_provenance(self) -> Question:
        if self.source is QuestionSource.COMPANY and not self.required:
            raise ValueError("company questions must remain required")
        if self.phase is QuestionPhase.ADAPTIVE and not self.source_question_id:
            raise ValueError("adaptive questions require source_question_id provenance")
        return self


class AdaptiveQuestionCandidate(BaseModel):
    id: str
    source_question_id: str
    competency_id: str
    text: str = Field(min_length=1)
    kind: Literal["deep_dive", "grilling", "inconsistency", "fact_check"] = "deep_dive"
    priority: int = Field(default=50, ge=0, le=100)
    status: QuestionStatus = QuestionStatus.PLANNED
    dedupe_key: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class InterviewPlan(BaseModel):
    id: str
    version: str = "1"
    job_title: str
    competencies: list[Competency]
    questions: list[Question]
    controls: InterviewPlanningControls | None = None
    max_redirects_per_question: int = Field(default=2, ge=0)
    approved: bool = False

    @model_validator(mode="after")
    def validate_question_bank(self) -> InterviewPlan:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")
        if self.controls is not None and len(self.questions) > self.controls.max_total_questions:
            raise ValueError("question bank exceeds max_total_questions")
        return self


class TranscriptTurn(BaseModel):
    id: str
    question_id: str
    original_text: str
    corrected_text: str | None = None
    correction_reason: str | None = None

    @property
    def effective_text(self) -> str:
        return self.corrected_text or self.original_text


class EvidenceItem(BaseModel):
    id: str
    question_id: str
    competency_id: str
    answer_text: str
    span: TextSpan
    confidence: float = Field(default=1, ge=0, le=1)


class CompetencyScore(BaseModel):
    competency_id: str
    score: int = Field(ge=0, le=4)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str
    insufficient_evidence: bool = False

    @model_validator(mode="after")
    def require_score_basis(self) -> CompetencyScore:
        if not self.evidence_ids and not self.insufficient_evidence:
            raise ValueError("a score requires evidence_ids or insufficient_evidence")
        if self.insufficient_evidence and self.score != 0:
            raise ValueError("insufficient evidence must use score 0")
        return self


class Assessment(BaseModel):
    competency_scores: list[CompetencyScore]
    weighted_score: float = Field(ge=0, le=4)
    evidence_coverage: float = Field(ge=0, le=1)
    model_version: str = "deterministic-v1"
    prompt_version: str = "evidence-score-v1"
    policy_version: str = "guardrails-v1"
    reviewer: str | None = None
    review_reason: str | None = None
    approved: bool = False


class BudgetState(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    input_audio_seconds: float = Field(default=0, ge=0)
    synthesized_characters: int = Field(default=0, ge=0)
    estimated_cost_usd: Decimal = Decimal("0")


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
