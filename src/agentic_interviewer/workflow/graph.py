from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agentic_interviewer.domain.models import (
    AnswerAnalysis,
    AnswerDisposition,
    Assessment,
    CandidateIntent,
    CompetencyScore,
    InterviewPhase,
    InterviewPlan,
    InterviewStatus,
    Question,
    QuestionStatus,
    Rubric,
)
from agentic_interviewer.policy import GuardrailPolicy
from agentic_interviewer.workflow.buffering import (
    consolidate_adaptive_candidates,
    deterministic_adaptive_candidate,
)
from agentic_interviewer.workflow.state import InterviewState


def _question(state: InterviewState):
    plan = InterviewPlan.model_validate(state["plan"])
    index = state["current_question_index"]
    phase = InterviewPhase(state.get("interview_phase", InterviewPhase.BASE))
    if phase is InterviewPhase.BASE:
        return plan.questions[index] if index < len(plan.questions) else None
    if phase is InterviewPhase.ADAPTIVE:
        adaptive_index = index - len(plan.questions)
        adaptive = state.get("adaptive_questions", [])
        return (
            Question.model_validate(adaptive[adaptive_index])
            if 0 <= adaptive_index < len(adaptive)
            else None
        )
    return None


def _clarification_for(question: Question | None) -> str:
    if question is None:
        return "The interview is complete."
    explanation = {
        "introduction": (
            "I'm asking for two things: a short overview of your relevant AI work, and why "
            "this role interests you now."
        ),
        "resume": (
            "Focus on one concrete example from your own work. Explain your personal role, "
            "the decision you made, and what changed as a result."
        ),
        "job": (
            "Describe a real example that shows how you handled this kind of work. Explain "
            "the situation, your decision, and the main trade-off."
        ),
        "company": (
            "Explain how you would approach the scenario, including your first action and "
            "the trade-off you would watch most closely."
        ),
        "adaptive": (
            "I'm asking for one missing detail from your previous answer. Give a concrete "
            "example, decision, or measurement."
        ),
    }.get(question.source.value, "Give one specific example and explain your role and result.")
    return f"Of course. {explanation}"


def _transition_to(question: Question, *, previous_status: QuestionStatus) -> str:
    if question.phase.value == "adaptive":
        return f"I'd like to follow up on that. {question.text}"
    if previous_status is QuestionStatus.SKIPPED:
        return f"No problem. Let's move on. {question.text}"
    return f"Thanks. Let's continue. {question.text}"


def _base_question_index(plan: InterviewPlan, question: Question) -> int | None:
    """Return the source base-question position for either kind of question."""
    source_id = question.source_question_id or question.id
    return next(
        (index for index, item in enumerate(plan.questions) if item.id == source_id),
        None,
    )


@dataclass
class InterviewWorkflow:
    graph: object

    def process(self, state: InterviewState) -> InterviewState:
        config = {
            "configurable": {"thread_id": state["interview_id"]},
            "recursion_limit": 12,
        }
        return cast(InterviewState, self.graph.invoke(state, config=config))  # type: ignore[attr-defined]

    def checkpoint(self, interview_id: str):
        config = {"configurable": {"thread_id": interview_id}}
        return self.graph.get_state(config)  # type: ignore[attr-defined]


def build_workflow(policy: GuardrailPolicy | None = None) -> InterviewWorkflow:
    guardrails = policy or GuardrailPolicy()

    def classify_intent(state: InterviewState) -> dict:
        decision = guardrails.classify(state["pending_answer"])
        analysis_raw = state.get("answer_analysis")
        if decision.primary_intent is CandidateIntent.ANSWER and analysis_raw:
            analysis = AnswerAnalysis.model_validate(analysis_raw)
            mapped = {
                AnswerDisposition.INSUFFICIENT: CandidateIntent.INSUFFICIENT_ANSWER,
                AnswerDisposition.GIBBERISH: CandidateIntent.UNINTELLIGIBLE,
                AnswerDisposition.SKIP: CandidateIntent.REFUSAL_OR_SKIP,
            }.get(analysis.disposition)
            if mapped is not None:
                decision = decision.model_copy(
                    update={
                        "primary_intent": mapped,
                        "confidence": analysis.confidence,
                        "matched_rule_ids": [f"semantic_{analysis.disposition.value}"],
                        "proposed_action": "clarify",
                    }
                )
        events = [*state["intent_events"], decision.model_dump(mode="json")]
        return {"intent_events": events}

    def apply_guardrail_policy(state: InterviewState) -> dict:
        decision = state["intent_events"][-1]
        intent = CandidateIntent(decision["primary_intent"])
        question = _question(state)
        if intent is CandidateIntent.STOP_OR_WITHDRAW:
            return {
                "status": InterviewStatus.WITHDRAWN,
                "last_response": "The interview has been stopped as requested.",
                "audit_events": [*state["audit_events"], {"type": "candidate_withdrew"}],
            }
        if intent is CandidateIntent.REPEAT_REQUEST:
            return {
                "last_response": question.text if question else "The interview is complete.",
                "audit_events": [*state["audit_events"], {"type": "question_repeated"}],
            }
        if intent is CandidateIntent.CLARIFICATION_REQUEST:
            return {
                "last_response": _clarification_for(question),
                "audit_events": [*state["audit_events"], {"type": "question_clarified"}],
            }
        if intent is CandidateIntent.CANDIDATE_QUESTION:
            current_text = question.text if question else ""
            return {
                "last_response": (
                    "I can note that question for the interviewer, but I cannot answer it "
                    "during the scored interview. Please answer the current question when "
                    f"ready: {current_text}"
                ),
                "audit_events": [
                    *state["audit_events"],
                    {"type": "candidate_question_deferred"},
                ],
            }
        if intent is CandidateIntent.ABUSIVE_OR_UNSAFE:
            current_text = question.text if question else ""
            return {
                "last_response": (
                    "Please keep the interview professional. You may answer the question, say "
                    "you do not know, or ask to move on. The current question is: "
                    f"{current_text}"
                ),
                "audit_events": [
                    *state["audit_events"],
                    {"type": "abusive_language_detected"},
                ],
            }
        if intent is CandidateIntent.TECHNICAL_PROBLEM:
            current_text = question.text if question else ""
            return {
                "last_response": (
                    "No problem. Resolve the audio or connection issue, then answer when ready. "
                    f"The current question is: {current_text}"
                ),
                "audit_events": [
                    *state["audit_events"],
                    {"type": "technical_problem_reported"},
                ],
            }
        if intent in {
            CandidateIntent.INSUFFICIENT_ANSWER,
            CandidateIntent.UNINTELLIGIBLE,
            CandidateIntent.UNCERTAIN,
        }:
            question_id = question.id if question else "complete"
            attempts = dict(state.get("answer_attempts_by_question", {}))
            attempts[question_id] = attempts.get(question_id, 0) + 1
            if attempts[question_id] >= 2:
                return {
                    "last_response": "",
                    "answer_attempts_by_question": attempts,
                    "question_statuses": {
                        **state.get("question_statuses", {}),
                        question_id: QuestionStatus.SKIPPED,
                    },
                    "question_history": [
                        *state.get("question_history", []),
                        {"question_id": question_id, "event": "retry_limit_reached"},
                    ],
                    "audit_events": [
                        *state["audit_events"],
                        {"type": "answer_retry_limit_reached", "question_id": question_id},
                    ],
                }
            if intent is CandidateIntent.UNINTELLIGIBLE:
                prompt = "I couldn't reliably understand that response. Please answer once more."
            elif state.get("answer_analysis"):
                prompt = AnswerAnalysis.model_validate(state["answer_analysis"]).feedback or (
                    "I need a little more detail before I can treat that as an answer."
                )
            else:
                prompt = "I need a little more detail before I can treat that as an answer."
            current_text = question.text if question else ""
            return {
                "last_response": f"{prompt} The question is: {current_text}",
                "answer_attempts_by_question": attempts,
                "audit_events": [
                    *state["audit_events"],
                    {"type": "answer_not_accepted", "intent": intent.value},
                ],
            }
        if intent is CandidateIntent.REFUSAL_OR_SKIP:
            question_id = question.id if question else "complete"
            return {
                "last_response": "",
                "question_statuses": {
                    **state.get("question_statuses", {}),
                    question_id: QuestionStatus.SKIPPED,
                },
                "question_history": [
                    *state.get("question_history", []),
                    {"question_id": question_id, "event": "skipped"},
                ],
                "audit_events": [
                    *state["audit_events"],
                    {"type": "question_skipped", "question_id": question_id},
                ],
            }
        if intent is not CandidateIntent.ANSWER:
            question_id = question.id if question else "complete"
            counts = dict(state["redirects_by_question"])
            counts[question_id] = counts.get(question_id, 0) + 1
            plan = InterviewPlan.model_validate(state["plan"])
            if counts[question_id] > plan.max_redirects_per_question:
                return {
                    "status": InterviewStatus.PAUSED,
                    "redirects_by_question": counts,
                    "last_response": (
                        "The interview has been paused after repeated attempts to change "
                        "its rules. A human reviewer can decide how to continue."
                    ),
                    "audit_events": [
                        *state["audit_events"],
                        {"type": "guardrail_paused", "intent": intent.value},
                    ],
                }
            else:
                current_text = question.text if question else ""
                response = (
                    "I cannot help with that. Please return to the interview question: "
                    f"{current_text}"
                )
            return {
                "redirects_by_question": counts,
                "last_response": response,
                "audit_events": [
                    *state["audit_events"],
                    {"type": "guardrail_redirect", "intent": intent.value},
                ],
            }
        return {"last_response": ""}

    def route_after_guardrail(
        state: InterviewState,
    ) -> Literal["extract_evidence", "advance", "finish_turn"]:
        decision = state["intent_events"][-1]
        intent = CandidateIntent(decision["primary_intent"])
        if state.get("audit_events", []) and state["audit_events"][-1].get("type") == (
            "answer_retry_limit_reached"
        ):
            return "advance"
        if intent is CandidateIntent.ANSWER:
            return "extract_evidence"
        if intent is CandidateIntent.REFUSAL_OR_SKIP:
            return "advance"
        return "finish_turn"

    def extract_evidence(state: InterviewState) -> dict:
        question = _question(state)
        if question is None:
            return {}
        answer = state["pending_answer"].strip()
        evidence = {
            "id": str(uuid4()),
            "question_id": question.id,
            "competency_id": question.competency_id,
            "answer_text": answer,
            "span": {"start": 0, "end": len(answer), "text": answer},
            "confidence": 1.0,
        }
        answer_record = {
            "id": str(uuid4()),
            "question_id": question.id,
            "original_text": answer,
            "corrected_text": None,
            "correction_reason": None,
        }
        plan = InterviewPlan.model_validate(state["plan"])
        candidates = list(state.get("adaptive_candidates", []))
        audit_events = list(state["audit_events"])
        phase = InterviewPhase(state.get("interview_phase", InterviewPhase.BASE))
        analysis = (
            AnswerAnalysis.model_validate(state["answer_analysis"])
            if state.get("answer_analysis")
            else None
        )
        if (
            phase is InterviewPhase.BASE
            and plan.controls is not None
            and analysis is not None
            and analysis.disposition is AnswerDisposition.FOLLOW_UP
        ):
            candidate = deterministic_adaptive_candidate(
                question=question, answer=answer, base_order=state["current_question_index"]
            )
            if candidate is not None:
                candidate = candidate.model_copy(update={"text": analysis.follow_up_question})
            if candidate is not None and not any(
                item.get("id") == candidate.id for item in candidates
            ):
                candidates.append(candidate.model_dump(mode="json"))
                audit_events.append(
                    {
                        "type": "adaptive_candidate_generated",
                        "candidate_id": candidate.id,
                        "source_question_id": question.id,
                        "kind": candidate.kind,
                    }
                )
        history = [
            *state.get("question_history", []),
            {
                "question_id": question.id,
                "phase": question.phase,
                "source": question.source,
                "event": "answered",
            },
        ]
        return {
            "answers": [*state["answers"], answer_record],
            "evidence": [*state["evidence"], evidence],
            "adaptive_candidates": candidates,
            "question_history": history,
            "question_statuses": {
                **state.get("question_statuses", {}),
                question.id: QuestionStatus.ANSWERED,
            },
            "audit_events": audit_events,
        }

    def complete_interview(state: InterviewState, next_index: int) -> dict:
        plan = InterviewPlan.model_validate(state["plan"])
        rubric = Rubric.model_validate(state["rubric"])
        scores: dict[str, float] = {}
        competency_scores: list[CompetencyScore] = []
        for competency in plan.competencies:
            citations = [
                item["id"] for item in state["evidence"] if item["competency_id"] == competency.id
            ]
            score = min(4, len(citations) * 2) if citations else 0
            scores[competency.id] = float(score)
            competency_scores.append(
                CompetencyScore(
                    competency_id=competency.id,
                    score=score,
                    evidence_ids=citations,
                    rationale=(
                        "Score is based only on the cited candidate response spans."
                        if citations
                        else "No relevant answer evidence was collected."
                    ),
                    insufficient_evidence=not citations,
                )
            )
        weights = {item.competency_id: item.weight for item in rubric.competencies}
        total_weight = sum(weights.get(item.competency_id, 0) for item in competency_scores)
        weighted_score = (
            sum(item.score * weights.get(item.competency_id, 0) for item in competency_scores)
            / total_weight
            if total_weight
            else 0
        )
        assessment = Assessment(
            competency_scores=competency_scores,
            weighted_score=weighted_score,
            evidence_coverage=(
                sum(bool(item.evidence_ids) for item in competency_scores) / len(competency_scores)
                if competency_scores
                else 0
            ),
        )
        return {
            "current_question_index": next_index,
            "interview_phase": InterviewPhase.COMPLETE,
            "status": InterviewStatus.COMPLETED,
            "scores": scores,
            "assessment": assessment.model_dump(mode="json"),
            "last_response": (
                "The interview is complete. Your responses will be reviewed by a human."
            ),
            "pending_answer": "",
            "answer_analysis": None,
            "audit_events": [*state["audit_events"], {"type": "interview_completed"}],
        }

    def advance(state: InterviewState) -> dict:
        plan = InterviewPlan.model_validate(state["plan"])
        phase = InterviewPhase(state.get("interview_phase", InterviewPhase.BASE))

        current_question = _question(state)
        if current_question is None:
            return complete_interview(state, state["current_question_index"])
        source_index = _base_question_index(plan, current_question)
        if source_index is None:
            # An adaptive question with invalid provenance cannot affect routing.
            return complete_interview(state, state["current_question_index"])

        adaptive_questions = [
            Question.model_validate(item) for item in state.get("adaptive_questions", [])
        ]
        selected = consolidate_adaptive_candidates(plan, state.get("adaptive_candidates", []))
        scheduled_ids = {item.id for item in adaptive_questions}
        newly_scheduled = [
            item
            for item in selected
            if item.source_question_id == plan.questions[source_index].id
            and item.id not in scheduled_ids
        ]
        adaptive_questions.extend(newly_scheduled)

        next_question: Question | None = None
        next_index: int
        if phase is InterviewPhase.BASE and newly_scheduled:
            next_question = newly_scheduled[0]
            next_index = len(plan.questions) + adaptive_questions.index(next_question)
        elif phase is InterviewPhase.ADAPTIVE:
            current_adaptive_index = state["current_question_index"] - len(plan.questions)
            next_adaptive_index = current_adaptive_index + 1
            if (
                next_adaptive_index < len(adaptive_questions)
                and adaptive_questions[next_adaptive_index].source_question_id
                == plan.questions[source_index].id
            ):
                next_question = adaptive_questions[next_adaptive_index]
                next_index = len(plan.questions) + next_adaptive_index
            else:
                next_index = source_index + 1
        else:
            next_index = source_index + 1

        previous_status = QuestionStatus(
            state.get("question_statuses", {}).get(current_question.id, QuestionStatus.ANSWERED)
        )
        if next_question is None:
            if next_index >= len(plan.questions):
                return complete_interview(state, next_index)
            next_question = plan.questions[next_index]

        audit_events = list(state["audit_events"])
        if newly_scheduled:
            audit_events.append(
                {
                    "type": "adaptive_questions_scheduled",
                    "source_question_id": plan.questions[source_index].id,
                    "question_ids": [item.id for item in newly_scheduled],
                    "selected_count": len(newly_scheduled),
                }
            )
        return {
            "adaptive_questions": [item.model_dump(mode="json") for item in adaptive_questions],
            "current_question_index": next_index,
            "interview_phase": next_question.phase,
            "last_response": _transition_to(next_question, previous_status=previous_status),
            "pending_answer": "",
            "answer_analysis": None,
            "question_history": [
                *state.get("question_history", []),
                {
                    "question_id": next_question.id,
                    "phase": next_question.phase,
                    "source": next_question.source,
                    "event": "asked",
                },
            ],
            "question_statuses": {
                **state.get("question_statuses", {}),
                **{
                    item.id: state.get("question_statuses", {}).get(item.id, QuestionStatus.PLANNED)
                    for item in newly_scheduled
                },
                next_question.id: QuestionStatus.ASKED,
            },
            "audit_events": audit_events,
        }

    def finish_turn(state: InterviewState) -> dict:
        return {"pending_answer": "", "answer_analysis": None}

    builder = StateGraph(InterviewState)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("apply_guardrail_policy", apply_guardrail_policy)
    builder.add_node("extract_evidence", extract_evidence)
    builder.add_node("advance", advance)
    builder.add_node("finish_turn", finish_turn)
    builder.add_edge(START, "classify_intent")
    builder.add_edge("classify_intent", "apply_guardrail_policy")
    builder.add_conditional_edges(
        "apply_guardrail_policy",
        route_after_guardrail,
        {
            "extract_evidence": "extract_evidence",
            "advance": "advance",
            "finish_turn": "finish_turn",
        },
    )
    builder.add_edge("extract_evidence", "advance")
    builder.add_edge("advance", END)
    builder.add_edge("finish_turn", END)
    return InterviewWorkflow(builder.compile(checkpointer=InMemorySaver()))
