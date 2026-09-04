from __future__ import annotations

import re
from collections import defaultdict
from hashlib import sha256

from agentic_interviewer.domain.models import (
    AdaptiveQuestionCandidate,
    InterviewPlan,
    Question,
    QuestionPhase,
    QuestionSource,
    QuestionStatus,
)


def deterministic_adaptive_candidate(
    *, question: Question, answer: str, base_order: int
) -> AdaptiveQuestionCandidate | None:
    """Reference behavior for offline tests and local development.

    A production reasoning adapter may emit richer candidates using the same
    schema. Keeping this bounded function outside the graph prevents provider
    concerns from leaking into state transitions.
    """
    answer = answer.strip()
    if not answer:
        return None
    anchor = _answer_anchor(answer)
    if not anchor:
        return None
    fingerprint = sha256(_semantic_key(anchor).encode("utf-8")).hexdigest()[:12]
    return AdaptiveQuestionCandidate(
        id=f"adaptive-candidate-{question.id}-{fingerprint}",
        source_question_id=question.id,
        competency_id=question.competency_id,
        text=(
            f'You said, "{anchor}" What specific decision or trade-off did you '
            "personally make, and what evidence supports that claim?"
        ),
        kind="deep_dive",
        priority=50,
        # Deliberately excludes the source question so repeated claims are
        # deduplicated across the complete base pass.
        dedupe_key=f"claim:{_semantic_key(anchor)}",
        provenance={
            "generator": "deterministic-v1",
            "source_question_order": base_order,
            "answer_character_count": len(answer),
            "answer_anchor": anchor,
            "answer_fingerprint": fingerprint,
        },
    )


def _answer_anchor(answer: str, limit: int = 140) -> str:
    normalized = re.sub(r"\s+", " ", answer).strip(" .")
    if not normalized:
        return ""
    statements = re.split(r"(?<=[.!?])\s+", normalized)
    first_statement = next(
        (statement for statement in statements if len(re.findall(r"\b\w+\b", statement)) >= 4),
        normalized,
    )
    if len(first_statement) <= limit:
        return first_statement
    shortened = first_statement[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{shortened}…"


def _semantic_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def consolidate_adaptive_candidates(
    plan: InterviewPlan,
    candidates: list[dict],
) -> list[Question]:
    """Validate, deduplicate and order the bounded adaptive question bank.

    The workflow may call this after every accepted answer. Source-question
    order remains stable across inference completion order, higher priority wins
    within one source question, and invalid/orphaned or deliberately retired
    candidates fail closed.
    """
    controls = plan.controls
    if controls is None or controls.max_adaptive_questions == 0:
        return []

    base_questions = [
        question for question in plan.questions if question.phase is QuestionPhase.BASE
    ]
    base_order = {question.id: index for index, question in enumerate(base_questions)}
    grouped: dict[str, list[AdaptiveQuestionCandidate]] = defaultdict(list)
    seen: set[str] = set()
    allowed_statuses = {QuestionStatus.PLANNED}

    parsed: list[AdaptiveQuestionCandidate] = []
    for raw in candidates:
        try:
            candidate = AdaptiveQuestionCandidate.model_validate(raw)
        except ValueError:
            continue
        if candidate.status not in allowed_statuses:
            continue
        if candidate.source_question_id not in base_order:
            continue
        parsed.append(candidate)

    parsed.sort(
        key=lambda item: (
            base_order[item.source_question_id],
            -item.priority,
            item.id,
        )
    )
    for candidate in parsed:
        key = candidate.dedupe_key or re.sub(r"\W+", " ", candidate.text.casefold()).strip()
        if key in seen:
            continue
        seen.add(key)
        grouped[candidate.source_question_id].append(candidate)

    remaining_total = max(0, controls.max_total_questions - len(base_questions))
    limit = min(controls.max_adaptive_questions, remaining_total)
    selected: list[AdaptiveQuestionCandidate] = []
    for question in base_questions:
        selected.extend(grouped.get(question.id, [])[: controls.max_adaptive_per_base_question])
        if len(selected) >= limit:
            selected = selected[:limit]
            break

    return [
        Question(
            id=f"adaptive-{candidate.id}",
            competency_id=candidate.competency_id,
            text=candidate.text,
            phase=QuestionPhase.ADAPTIVE,
            source=QuestionSource.ADAPTIVE,
            source_question_id=candidate.source_question_id,
            provenance={
                **candidate.provenance,
                "candidate_id": candidate.id,
                "candidate_kind": candidate.kind,
                "candidate_priority": candidate.priority,
            },
        )
        for candidate in selected
    ]
