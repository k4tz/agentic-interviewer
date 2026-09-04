from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from agentic_interviewer.domain.models import (
    AdaptiveQuestionCandidate,
    Assessment,
    CompanyQuestionPolicy,
    Competency,
    CompetencyScore,
    InterviewPhase,
    InterviewPlan,
    InterviewPlanningControls,
    InterviewStatus,
    JobProfile,
    Question,
    QuestionSource,
    QuestionStatus,
    ResumeProfile,
    Rubric,
    RubricCompetency,
    ScoreAnchor,
    SourceClaim,
    TextSpan,
)
from agentic_interviewer.persistence.contracts import InterviewRepository, NotFoundError
from agentic_interviewer.persistence.models import ImmutableDocument, SessionRecord
from agentic_interviewer.services.question_planning import (
    build_grounded_fallback_bank,
    question_provenance,
)
from agentic_interviewer.workflow import InterviewWorkflow, build_workflow
from agentic_interviewer.workflow.state import InterviewState


class InterviewNotFoundError(KeyError):
    pass


class InterviewConflictError(RuntimeError):
    pass


class InterviewService:
    def __init__(
        self,
        workflow: InterviewWorkflow | None = None,
        repository: InterviewRepository | None = None,
    ) -> None:
        self._workflow = workflow or build_workflow()
        self._repository = repository
        self._items: dict[str, InterviewState] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        job_title: str,
        resume_text: str,
        job_details: str,
        tenant_id: str = "default",
        candidate_id: str | None = None,
        planning_controls: InterviewPlanningControls | dict | None = None,
        company_questions: list[str] | None = None,
    ) -> InterviewState:
        interview_id = str(uuid4())
        candidate_id = candidate_id or str(uuid4())
        controls = (
            InterviewPlanningControls.model_validate(planning_controls)
            if planning_controls is not None
            else None
        )
        competency = Competency(
            id="experience",
            name="Relevant experience",
            description="Evidence of relevant, personally performed work.",
        )
        questions = (
            self._build_buffered_base_questions(
                competency_id=competency.id,
                job_title=job_title,
                resume_text=resume_text,
                job_details=job_details,
                controls=controls,
                company_questions=company_questions or [],
            )
            if controls is not None
            else [
                Question(
                    id="experience-overview",
                    competency_id=competency.id,
                    text=(
                        "Tell me about experience from your resume that is most relevant "
                        f"to the {job_title} role."
                    ),
                ),
                Question(
                    id="problem-solving",
                    competency_id=competency.id,
                    text=(
                        "Describe a difficult problem you personally solved and the measurable "
                        "result."
                    ),
                ),
            ]
        )
        plan = InterviewPlan(
            id=str(uuid4()),
            job_title=job_title,
            competencies=[competency],
            questions=questions,
            controls=controls,
        )
        rubric = Rubric(
            id=str(uuid4()),
            version="1",
            competencies=[
                RubricCompetency(
                    competency_id=competency.id,
                    weight=1,
                    anchors=[
                        ScoreAnchor(score=0, description="Insufficient cited evidence"),
                        ScoreAnchor(score=2, description="Relevant cited evidence"),
                        ScoreAnchor(score=4, description="Multiple strong cited examples"),
                    ],
                )
            ],
            prohibited_criteria=[
                "protected traits",
                "voice or accent characteristics",
                "emotion or sentiment inference",
                "guardrail or moderation labels",
            ],
        )
        resume_profile = ResumeProfile(
            id=str(uuid4()),
            candidate_id=candidate_id,
            claims=[self._claim("resume-claim-1", "experience", resume_text, "resume")],
            redacted_fields={"date_of_birth", "address", "nationality", "marital_status"},
        )
        job_profile = JobProfile(
            id=str(uuid4()),
            title=job_title,
            requirements=[self._claim("job-claim-1", "requirement", job_details, "job")],
            prohibited_topics=[
                "age",
                "disability",
                "family status",
                "nationality",
                "religion",
            ],
        )
        state: InterviewState = {
            "interview_id": interview_id,
            "tenant_id": tenant_id,
            "candidate_id": candidate_id,
            "resume_profile": resume_profile.model_dump(mode="json"),
            "job_profile": job_profile.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "rubric": rubric.model_dump(mode="json"),
            "status": InterviewStatus.CREATED,
            "current_question_index": 0,
            "interview_phase": InterviewPhase.BASE,
            "adaptive_candidates": [],
            "adaptive_questions": [],
            "question_history": [],
            "question_statuses": {
                question.id: QuestionStatus.PLANNED for question in plan.questions
            },
            "pending_answer": "",
            "answer_analysis": None,
            "last_response": "",
            "answers": [],
            "evidence": [],
            "scores": {},
            "intent_events": [],
            "redirects_by_question": {},
            "answer_attempts_by_question": {},
            "audit_events": [
                {
                    "type": "interview_created",
                    "resume_length": len(resume_text),
                    "job_details_length": len(job_details),
                }
            ],
            "assessment": None,
            "usage": [],
            "appeals": [],
            "export": None,
        }
        with self._lock:
            self._items[interview_id] = state
            return self._commit(state)

    def get(self, interview_id: str) -> InterviewState:
        with self._lock:
            if interview_id not in self._items and self._repository is not None:
                try:
                    record = self._repository.get_session("default", interview_id)
                except NotFoundError as exc:
                    raise InterviewNotFoundError(interview_id) from exc
                self._items[interview_id] = record.payload  # type: ignore[assignment]
            try:
                return deepcopy(self._items[interview_id])
            except KeyError as exc:
                raise InterviewNotFoundError(interview_id) from exc

    def get_for_tenant(self, tenant_id: str, interview_id: str) -> InterviewState:
        with self._lock:
            state = self._items.get(interview_id)
            if state is None and self._repository is not None:
                try:
                    record = self._repository.get_session(tenant_id, interview_id)
                except NotFoundError as exc:
                    raise InterviewNotFoundError(interview_id) from exc
                state = record.payload  # type: ignore[assignment]
                self._items[interview_id] = state
            if state is None or state["tenant_id"] != tenant_id:
                raise InterviewNotFoundError(interview_id)
            return deepcopy(state)

    def start(self, interview_id: str) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.READY:
                raise InterviewConflictError("approved plan and rubric are required before start")
            plan = InterviewPlan.model_validate(state["plan"])
            if not plan.questions:
                raise InterviewConflictError("approved plan has no questions")
            state["status"] = InterviewStatus.IN_PROGRESS
            state["last_response"] = plan.questions[0].text
            state["interview_phase"] = InterviewPhase.BASE
            state["question_statuses"] = {
                **state.get("question_statuses", {}),
                plan.questions[0].id: QuestionStatus.ASKED,
            }
            state["question_history"].append(
                {
                    "question_id": plan.questions[0].id,
                    "phase": plan.questions[0].phase,
                    "source": plan.questions[0].source,
                    "event": "asked",
                }
            )
            state["audit_events"].append(
                {"type": "interview_started", "question_id": plan.questions[0].id}
            )
            return self._commit(state)

    def create_buffered(
        self,
        *,
        job_title: str,
        resume_text: str,
        job_details: str,
        tenant_id: str = "default",
        candidate_id: str | None = None,
        planning_controls: InterviewPlanningControls | dict | None = None,
        company_questions: list[str] | None = None,
    ) -> InterviewState:
        """Create a bounded interview using the 1 + 5 + 5 base-question contract."""
        return self.create(
            job_title=job_title,
            resume_text=resume_text,
            job_details=job_details,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            planning_controls=(
                InterviewPlanningControls.model_validate(planning_controls)
                if planning_controls is not None
                else InterviewPlanningControls()
            ),
            company_questions=company_questions,
        )

    def customize_base_questions(
        self,
        interview_id: str,
        *,
        introduction: list[str],
        resume: list[str],
        job: list[str],
        generator: str,
        generated_provenance: dict[QuestionSource, list[dict]] | None = None,
    ) -> InterviewState:
        """Replace draft base-question wording while preserving allocation and provenance."""
        proposed = {
            QuestionSource.INTRODUCTION: introduction,
            QuestionSource.RESUME: resume,
            QuestionSource.JOB: job,
        }
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.CREATED:
                raise InterviewConflictError(
                    "questions can only be customized before setup approval"
                )
            plan = InterviewPlan.model_validate(state["plan"])
            updated_questions = list(plan.questions)
            for source, texts in proposed.items():
                targets = [
                    index
                    for index, question in enumerate(updated_questions)
                    if question.source is source
                ]
                if len(texts) != len(targets):
                    raise InterviewConflictError(
                        f"{source.value} question count does not match the approved allocation"
                    )
                source_provenance = (generated_provenance or {}).get(source, [])
                if source_provenance and len(source_provenance) != len(texts):
                    raise InterviewConflictError(
                        f"{source.value} question provenance count does not match its allocation"
                    )
                for item_index, (target, raw_text) in enumerate(zip(targets, texts, strict=True)):
                    text = raw_text.strip()
                    if not text or len(text) > 1_000:
                        raise InterviewConflictError(
                            "generated questions must contain between 1 and 1000 characters"
                        )
                    question = updated_questions[target]
                    generated = source_provenance[item_index] if source_provenance else {}
                    updated_questions[target] = question.model_copy(
                        update={
                            "text": text,
                            "provenance": {
                                **question.provenance,
                                "generator": generator,
                                **generated,
                            },
                        }
                    )
            state["plan"] = plan.model_copy(update={"questions": updated_questions}).model_dump(
                mode="json"
            )
            state["audit_events"].append(
                {
                    "type": "base_questions_customized",
                    "generator": generator,
                    "question_count": sum(len(items) for items in proposed.values()),
                }
            )
            return self._commit(state)

    def add_adaptive_candidates(
        self, interview_id: str, candidates: list[AdaptiveQuestionCandidate | dict]
    ) -> InterviewState:
        """Accept structured reasoning output without coupling to a model vendor."""
        with self._lock:
            state = self._require(interview_id)
            if state.get("status") in {
                InterviewStatus.COMPLETED,
                InterviewStatus.APPROVED,
                InterviewStatus.WITHDRAWN,
            }:
                raise InterviewConflictError(
                    "adaptive candidates are closed for terminal interviews"
                )
            existing = {
                item["id"]: AdaptiveQuestionCandidate.model_validate(item)
                for item in state.get("adaptive_candidates", [])
            }
            added: list[str] = []
            for raw in candidates:
                candidate = AdaptiveQuestionCandidate.model_validate(raw)
                if candidate.id not in existing:
                    added.append(candidate.id)
                existing[candidate.id] = candidate
            state["adaptive_candidates"] = [
                item.model_dump(mode="json") for item in existing.values()
            ]
            state["audit_events"].append(
                {"type": "adaptive_candidates_received", "candidate_ids": added}
            )
            return self._commit(state)

    def retire_adaptive_candidate(
        self,
        interview_id: str,
        candidate_id: str,
        *,
        status: QuestionStatus,
        reason: str,
    ) -> InterviewState:
        if status not in {QuestionStatus.COVERED_ELSEWHERE, QuestionStatus.OBSOLETE}:
            raise InterviewConflictError("candidate can only be covered or obsolete")
        with self._lock:
            state = self._require(interview_id)
            candidates = state.get("adaptive_candidates", [])
            raw = next((item for item in candidates if item["id"] == candidate_id), None)
            if raw is None:
                raise InterviewNotFoundError(candidate_id)
            candidate = AdaptiveQuestionCandidate.model_validate(raw).model_copy(
                update={"status": status}
            )
            candidates[candidates.index(raw)] = candidate.model_dump(mode="json")
            state["audit_events"].append(
                {
                    "type": "adaptive_candidate_retired",
                    "candidate_id": candidate_id,
                    "status": status,
                    "reason": reason,
                }
            )
            return self._commit(state)

    def correct_claim(
        self, interview_id: str, profile: str, claim_id: str, value: str, actor: str
    ) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.CREATED:
                raise InterviewConflictError("approved source profiles are immutable")
            key = {"resume": "resume_profile", "job": "job_profile"}.get(profile)
            if key is None:
                raise InterviewConflictError("profile must be resume or job")
            claims_key = "claims" if profile == "resume" else "requirements"
            claims = state[key][claims_key]
            claim = next((item for item in claims if item["id"] == claim_id), None)
            if claim is None:
                raise InterviewNotFoundError(claim_id)
            prior_hash_basis = claim["value"]
            claim["value"] = value
            claim["span"] = {"start": 0, "end": len(value), "text": value}
            state["audit_events"].append(
                {
                    "type": "source_claim_corrected",
                    "profile": profile,
                    "claim_id": claim_id,
                    "actor": actor,
                    "previous_length": len(prior_hash_basis),
                }
            )
            return self._commit(state)

    def approve_setup(self, interview_id: str, reviewer: str, reason: str) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.CREATED:
                raise InterviewConflictError("setup can only be approved once")
            plan = InterviewPlan.model_validate(state["plan"]).model_copy(update={"approved": True})
            rubric = Rubric.model_validate(state["rubric"]).model_copy(update={"approved": True})
            plan_competencies = {item.id for item in plan.competencies}
            rubric_competencies = {item.competency_id for item in rubric.competencies}
            if plan_competencies != rubric_competencies:
                raise InterviewConflictError("plan and rubric competencies do not match")
            state["plan"] = plan.model_dump(mode="json")
            state["rubric"] = rubric.model_dump(mode="json")
            state["status"] = InterviewStatus.READY
            state["audit_events"].append(
                {"type": "setup_approved", "reviewer": reviewer, "reason": reason}
            )
            if self._repository is not None:
                self._repository.put_plan(
                    ImmutableDocument(
                        state["tenant_id"], plan.id, plan.version, plan.model_dump(mode="json")
                    )
                )
                self._repository.put_rubric(
                    ImmutableDocument(
                        state["tenant_id"],
                        rubric.id,
                        rubric.version,
                        rubric.model_dump(mode="json"),
                    )
                )
            return self._commit(state)

    def answer(self, interview_id: str, text: str, analysis: dict | None = None) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.IN_PROGRESS:
                raise InterviewConflictError("interview is not accepting answers")
            state["pending_answer"] = text
            state["answer_analysis"] = analysis
            updated = self._workflow.process(deepcopy(state))
            self._items[interview_id] = updated
            return self._commit(updated)

    def approve(self, interview_id: str, reviewer: str, reason: str) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.COMPLETED:
                raise InterviewConflictError("only completed interviews can be approved")
            assessment = Assessment.model_validate(state["assessment"])
            state["assessment"] = assessment.model_copy(
                update={"reviewer": reviewer, "review_reason": reason, "approved": True}
            ).model_dump(mode="json")
            state["status"] = InterviewStatus.APPROVED
            state["audit_events"].append(
                {"type": "review_approved", "reviewer": reviewer, "reason": reason}
            )
            return self._commit(state)

    def correct_transcript(
        self, interview_id: str, turn_id: str, corrected_text: str, reason: str
    ) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] not in {InterviewStatus.IN_PROGRESS, InterviewStatus.COMPLETED}:
                raise InterviewConflictError("transcript is not correctable in this state")
            turn = next((item for item in state["answers"] if item["id"] == turn_id), None)
            if turn is None:
                raise InterviewNotFoundError(turn_id)
            previous_effective_text = turn.get("corrected_text") or turn["original_text"]
            turn["corrected_text"] = corrected_text
            turn["correction_reason"] = reason
            for evidence in state["evidence"]:
                if evidence["question_id"] == turn["question_id"] and (
                    evidence["answer_text"] == turn["original_text"]
                    or evidence["answer_text"] == previous_effective_text
                ):
                    evidence["answer_text"] = corrected_text
                    evidence["span"] = {
                        "start": 0,
                        "end": len(corrected_text),
                        "text": corrected_text,
                    }
            state["audit_events"].append(
                {"type": "transcript_corrected", "turn_id": turn_id, "reason": reason}
            )
            return self._commit(state)

    def override_assessment(
        self,
        interview_id: str,
        reviewer: str,
        reason: str,
        competency_scores: list[dict],
    ) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.COMPLETED:
                raise InterviewConflictError("only completed assessments can be overridden")
            parsed = [CompetencyScore.model_validate(item) for item in competency_scores]
            if not parsed:
                raise InterviewConflictError("override must include every rubric competency")
            evidence_ids = {item["id"] for item in state["evidence"]}
            if any(not set(item.evidence_ids) <= evidence_ids for item in parsed):
                raise InterviewConflictError("override references unknown evidence")
            rubric = Rubric.model_validate(state["rubric"])
            weights = {item.competency_id: item.weight for item in rubric.competencies}
            if {item.competency_id for item in parsed} != set(weights):
                raise InterviewConflictError("override must match the approved rubric")
            evidence_competencies = {
                item["id"]: item["competency_id"] for item in state["evidence"]
            }
            if any(
                any(
                    evidence_competencies[evidence_id] != item.competency_id
                    for evidence_id in item.evidence_ids
                )
                for item in parsed
            ):
                raise InterviewConflictError("override evidence belongs to another competency")
            total = sum(weights.get(item.competency_id, 0) for item in parsed)
            weighted = (
                sum(item.score * weights.get(item.competency_id, 0) for item in parsed) / total
                if total
                else 0
            )
            state["assessment"] = Assessment(
                competency_scores=parsed,
                weighted_score=weighted,
                evidence_coverage=sum(bool(item.evidence_ids) for item in parsed) / len(parsed),
                reviewer=reviewer,
                review_reason=reason,
            ).model_dump(mode="json")
            state["scores"] = {item.competency_id: float(item.score) for item in parsed}
            state["audit_events"].append(
                {"type": "assessment_overridden", "reviewer": reviewer, "reason": reason}
            )
            return self._commit(state)

    def appeal(self, interview_id: str, candidate_reason: str) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] not in {InterviewStatus.COMPLETED, InterviewStatus.APPROVED}:
                raise InterviewConflictError("appeals require a completed interview")
            appeal = {
                "id": str(uuid4()),
                "reason": candidate_reason,
                "status": "pending_human_review",
                "created_at": datetime.now(UTC).isoformat(),
            }
            state["appeals"].append(appeal)
            state["audit_events"].append(
                {"type": "candidate_appeal_requested", "appeal_id": appeal["id"]}
            )
            return self._commit(state)

    def export(self, interview_id: str, idempotency_key: str) -> dict:
        with self._lock:
            state = self._require(interview_id)
            if state["status"] != InterviewStatus.APPROVED:
                raise InterviewConflictError("only human-approved assessments can be exported")
            if state["export"] is not None:
                if state["export"]["idempotency_key"] != idempotency_key:
                    raise InterviewConflictError("assessment was already exported")
                return deepcopy(state["export"])
            assessment = Assessment.model_validate(state["assessment"])
            artifact = {
                "interview_id": interview_id,
                "tenant_id": state["tenant_id"],
                "plan_id": state["plan"]["id"],
                "plan_version": state["plan"]["version"],
                "rubric_id": state["rubric"]["id"],
                "rubric_version": state["rubric"]["version"],
                "assessment": assessment.model_dump(mode="json"),
                "idempotency_key": idempotency_key,
                "exported_at": datetime.now(UTC).isoformat(),
            }
            state["export"] = artifact
            state["audit_events"].append(
                {"type": "assessment_exported", "idempotency_key": idempotency_key}
            )
            self._commit(state)
            return deepcopy(artifact)

    def checkpoint(self, interview_id: str):
        self._require(interview_id)
        return self._workflow.checkpoint(interview_id)

    def record_usage(self, interview_id: str, usage: dict) -> InterviewState:
        with self._lock:
            state = self._require(interview_id)
            state["usage"].append(usage)
            return self._commit(state)

    def _require(self, interview_id: str) -> InterviewState:
        try:
            return self._items[interview_id]
        except KeyError as exc:
            if self._repository is None:
                raise InterviewNotFoundError(interview_id) from exc
            try:
                record = self._repository.get_session("default", interview_id)
            except NotFoundError as repository_exc:
                raise InterviewNotFoundError(interview_id) from repository_exc
            self._items[interview_id] = record.payload  # type: ignore[assignment]
            return self._items[interview_id]

    def _commit(self, state: InterviewState) -> InterviewState:
        self._items[state["interview_id"]] = state
        if self._repository is None:
            return deepcopy(state)
        key = ":".join(
            (
                state["interview_id"],
                str(len(state["audit_events"])),
                str(len(state["answers"])),
                str(len(state["usage"])),
                str(state["status"]),
            )
        )
        try:
            current = self._repository.get_session(state["tenant_id"], state["interview_id"])
        except NotFoundError:
            self._repository.create_session(
                SessionRecord(
                    tenant_id=state["tenant_id"],
                    session_id=state["interview_id"],
                    plan_id=state["plan"]["id"],
                    status=str(state["status"]),
                    payload=deepcopy(state),
                ),
                idempotency_key=key,
            )
        else:
            self._repository.update_session(
                state["tenant_id"],
                state["interview_id"],
                expected_revision=current.revision,
                status=str(state["status"]),
                payload=deepcopy(state),
                idempotency_key=key,
            )
        event = state["audit_events"][-1]
        self._repository.append_audit(
            state["tenant_id"],
            event_type=event["type"],
            actor=str(event.get("actor") or event.get("reviewer") or "system"),
            detail={name: value for name, value in event.items() if name != "type"},
            request_id=key,
            policy_version="guardrails-v1",
            idempotency_key=f"audit:{key}",
            session_id=state["interview_id"],
        )
        return deepcopy(state)

    @staticmethod
    def _build_buffered_base_questions(
        *,
        competency_id: str,
        job_title: str,
        resume_text: str,
        job_details: str,
        controls: InterviewPlanningControls,
        company_questions: list[str],
    ) -> list[Question]:
        questions: list[Question] = []
        focus_schedule = InterviewService._focus_schedule(
            controls, controls.resume_questions + controls.job_questions
        )
        expected_counts = {
            "introduction": controls.introduction_questions,
            "resume": controls.resume_questions,
            "job": controls.job_questions,
        }
        fallback, resume_items, job_items = build_grounded_fallback_bank(
            job_title=job_title,
            resume_text=resume_text,
            job_details=job_details,
            expected_counts=expected_counts,
        )
        for index, planned in enumerate(fallback.introduction):
            questions.append(
                Question(
                    id=f"introduction-{index + 1}",
                    competency_id=competency_id,
                    text=planned.text,
                    source=QuestionSource.INTRODUCTION,
                    provenance={"allocation": "introduction", "order": index},
                )
            )
        for index, planned in enumerate(fallback.resume):
            focus_area = focus_schedule[index]
            questions.append(
                Question(
                    id=f"resume-{index + 1}",
                    competency_id=competency_id,
                    text=planned.text,
                    source=QuestionSource.RESUME,
                    provenance={
                        "allocation": "resume",
                        "order": index,
                        "focus_area": focus_area,
                        **question_provenance(
                            planned, resume_items, generator="deterministic_grounded"
                        ),
                    },
                )
            )
        for index, planned in enumerate(fallback.job):
            focus_area = focus_schedule[controls.resume_questions + index]
            questions.append(
                Question(
                    id=f"job-{index + 1}",
                    competency_id=competency_id,
                    text=planned.text,
                    source=QuestionSource.JOB,
                    provenance={
                        "allocation": "job",
                        "order": index,
                        "focus_area": focus_area,
                        **question_provenance(
                            planned, job_items, generator="deterministic_grounded"
                        ),
                    },
                )
            )

        bounded_company = [text.strip() for text in company_questions if text.strip()]
        if len(bounded_company) > controls.max_company_questions:
            raise InterviewConflictError(
                "company question count exceeds max_company_questions; required questions "
                "cannot be silently dropped"
            )
        company_bank = [
            Question(
                id=f"company-{index + 1}",
                competency_id=competency_id,
                text=text,
                source=QuestionSource.COMPANY,
                required=True,
                provenance={"allocation": "company_required", "order": index},
            )
            for index, text in enumerate(bounded_company)
        ]
        if controls.company_question_policy is CompanyQuestionPolicy.APPEND_WITHIN_TOTAL_BUDGET:
            room = max(0, controls.max_total_questions - len(questions))
            if len(company_bank) > room:
                raise InterviewConflictError(
                    "required company questions exceed the append capacity"
                )
            questions.extend(company_bank)
            return questions

        replaceable = [
            index
            for source in (QuestionSource.JOB, QuestionSource.RESUME)
            for index in range(len(questions))
            if questions[index].source is source
        ]
        if len(company_bank) > len(replaceable):
            raise InterviewConflictError(
                "required company questions exceed the replacement capacity"
            )
        targets = sorted(replaceable[: len(company_bank)])
        for replacement, target in zip(company_bank, targets, strict=False):
            questions[target] = replacement
        return questions

    @staticmethod
    def _focus_schedule(controls: InterviewPlanningControls, question_count: int) -> list[str]:
        """Allocate focus areas with deterministic largest-remainder rounding."""
        if question_count == 0:
            return []
        weights = controls.focus.model_dump()
        total = sum(weights.values())
        quotas = {name: question_count * weight / total for name, weight in weights.items()}
        counts = {name: int(quota) for name, quota in quotas.items()}
        remaining = question_count - sum(counts.values())
        ranked = sorted(
            weights,
            key=lambda name: (-(quotas[name] - counts[name]), -weights[name], name),
        )
        for name in ranked[:remaining]:
            counts[name] += 1
        return [name for name in weights for _ in range(counts[name])]

    @staticmethod
    def _claim(claim_id: str, kind: str, value: str, source: str) -> SourceClaim:
        return SourceClaim(
            id=claim_id,
            kind=kind,
            value=value,
            source_document=source,
            span=TextSpan(start=0, end=len(value), text=value),
        )
