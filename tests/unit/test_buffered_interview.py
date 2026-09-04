import pytest

from agentic_interviewer.domain.models import (
    AdaptiveQuestionCandidate,
    CompanyQuestionPolicy,
    InterviewFocus,
    InterviewPlanningControls,
    InterviewStatus,
    QuestionSource,
    QuestionStatus,
)
from agentic_interviewer.services import InterviewService
from agentic_interviewer.services.interviews import InterviewConflictError


def _approve_and_start(service: InterviewService, interview_id: str) -> dict:
    service.approve_setup(interview_id, "reviewer", "verified")
    return service.start(interview_id)


def _follow_up(text: str = "What measurable result confirmed that decision?") -> dict:
    return {
        "disposition": "follow_up",
        "confidence": 0.9,
        "feedback": "",
        "follow_up_question": text,
    }


def _accept() -> dict:
    return {
        "disposition": "accept",
        "confidence": 0.9,
        "feedback": "",
        "follow_up_question": None,
    }


def test_default_buffered_contract_allocates_one_five_five_and_required_company_slots():
    service = InterviewService()
    created = service.create_buffered(
        job_title="Platform Engineer",
        resume_text="Built distributed services",
        job_details="Operate reliable systems",
        company_questions=["Explain a company-specific incident response decision."],
    )

    questions = created["plan"]["questions"]
    assert len(questions) == 11
    assert [item["source"] for item in questions].count("introduction") == 1
    assert [item["source"] for item in questions].count("resume") == 5
    assert [item["source"] for item in questions].count("job") == 4
    company = next(item for item in questions if item["source"] == "company")
    assert company["required"] is True


def test_required_company_question_overflow_fails_closed():
    controls = InterviewPlanningControls(max_company_questions=1)
    service = InterviewService()

    with pytest.raises(InterviewConflictError, match="cannot be silently dropped"):
        service.create_buffered(
            job_title="Platform Engineer",
            resume_text="Built systems",
            job_details="Reliable operations",
            planning_controls=controls,
            company_questions=["Required one", "Required two"],
        )


def test_focus_vector_changes_traceable_question_allocation():
    service = InterviewService()
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=2,
        job_questions=2,
        max_company_questions=0,
        max_adaptive_questions=0,
        max_total_questions=4,
        focus=InterviewFocus(
            problem_solving=0,
            system_design=0,
            technical_depth=1,
            behavioral=0,
        ),
    )

    created = service.create_buffered(
        job_title="Platform Engineer",
        resume_text="Built distributed systems",
        job_details="Design and operate services",
        planning_controls=controls,
    )

    assert {item["provenance"]["focus_area"] for item in created["plan"]["questions"]} == {
        "technical_depth"
    }


def test_company_questions_can_extend_an_explicit_total_budget():
    service = InterviewService()
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=1,
        job_questions=1,
        company_question_policy=CompanyQuestionPolicy.APPEND_WITHIN_TOTAL_BUDGET,
        max_company_questions=1,
        max_adaptive_questions=0,
        max_total_questions=3,
    )

    created = service.create_buffered(
        job_title="Platform Engineer",
        resume_text="Built distributed systems",
        job_details="Design and operate services",
        planning_controls=controls,
        company_questions=["Explain how you would apply our incident policy."],
    )

    assert [item["source"] for item in created["plan"]["questions"]] == [
        "resume",
        "job",
        "company",
    ]


def test_adaptive_answer_extends_evidence_and_completes_conversational_turn():
    service = InterviewService()
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=1,
        job_questions=0,
        max_company_questions=0,
        max_adaptive_questions=1,
        max_adaptive_per_base_question=1,
        max_total_questions=2,
    )
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built a service",
        job_details="Operate services",
        planning_controls=controls,
    )
    interview_id = created["interview_id"]
    _approve_and_start(service, interview_id)

    adaptive = service.answer(
        interview_id, "I selected a queue and measured latency.", _follow_up()
    )
    assert adaptive["interview_phase"] == "adaptive"
    completed = service.answer(
        interview_id,
        "I chose bounded retries because they reduced tail latency without hiding failure.",
        _accept(),
    )

    assert completed["status"] == InterviewStatus.COMPLETED
    assert completed["interview_phase"] == "complete"
    assert len(completed["evidence"]) == 2


def test_late_reasoning_candidate_can_extend_the_current_conversational_follow_up():
    service = InterviewService()
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=1,
        job_questions=0,
        max_company_questions=0,
        max_adaptive_questions=2,
        max_adaptive_per_base_question=2,
        max_total_questions=3,
    )
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built a service",
        job_details="Operate services",
        planning_controls=controls,
    )
    interview_id = created["interview_id"]
    source_question_id = created["plan"]["questions"][0]["id"]
    _approve_and_start(service, interview_id)

    first_probe = service.answer(
        interview_id,
        "I selected a queue and measured latency against a baseline.",
        _follow_up(),
    )
    assert first_probe["interview_phase"] == "adaptive"

    service.add_adaptive_candidates(
        interview_id,
        [
            AdaptiveQuestionCandidate(
                id="late-model-probe",
                source_question_id=source_question_id,
                competency_id="experience",
                text="How did you establish that latency baseline?",
                priority=100,
            )
        ],
    )
    second_probe = service.answer(
        interview_id,
        "I owned the decision and compared p95 before and after the rollout.",
        _accept(),
    )

    assert second_probe["interview_phase"] == "adaptive"
    assert second_probe["last_response"].endswith("How did you establish that latency baseline?")
    assert len(second_probe["adaptive_questions"]) == 2


def test_accepted_base_answer_gets_immediate_probe_then_returns_to_base_plan():
    service = InterviewService()
    created = service.create_buffered(
        job_title="Backend Engineer",
        resume_text="Built APIs",
        job_details="Distributed systems",
    )
    interview_id = created["interview_id"]
    started = _approve_and_start(service, interview_id)
    approved_plan = started["plan"]

    state = service.answer(
        interview_id,
        "I selected a queue, documented the trade-off, and measured p95 latency.",
        _follow_up(),
    )
    assert state["status"] == InterviewStatus.IN_PROGRESS
    assert state["interview_phase"] == "adaptive"
    assert len(state["adaptive_questions"]) == 1
    assert (
        state["adaptive_questions"][0]["source_question_id"] == approved_plan["questions"][0]["id"]
    )
    assert state["plan"] == approved_plan

    state = service.answer(
        interview_id,
        "I chose bounded retries and verified the result with load-test percentiles.",
        _accept(),
    )
    assert state["interview_phase"] == "base"
    assert state["current_question_index"] == 1
    assert state["last_response"].endswith(approved_plan["questions"][1]["text"])
    assert len(state["adaptive_questions"]) == 1


def test_consolidation_is_source_ordered_deduplicated_bounded_and_removes_retired_items():
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=2,
        job_questions=0,
        max_company_questions=0,
        max_adaptive_questions=2,
        max_adaptive_per_base_question=1,
        max_total_questions=4,
    )
    service = InterviewService()
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Two projects",
        job_details="Engineering",
        planning_controls=controls,
    )
    interview_id = created["interview_id"]
    first_id, second_id = [item["id"] for item in created["plan"]["questions"]]
    service.add_adaptive_candidates(
        interview_id,
        [
            AdaptiveQuestionCandidate(
                id="second-high",
                source_question_id=second_id,
                competency_id="experience",
                text="Second source probe",
                priority=100,
                dedupe_key="second",
            ),
            AdaptiveQuestionCandidate(
                id="first-high",
                source_question_id=first_id,
                competency_id="experience",
                text="First source probe",
                priority=90,
                dedupe_key="first",
            ),
            AdaptiveQuestionCandidate(
                id="first-duplicate",
                source_question_id=first_id,
                competency_id="experience",
                text="Duplicate first source probe",
                priority=80,
                dedupe_key="first",
            ),
            AdaptiveQuestionCandidate(
                id="retired",
                source_question_id=second_id,
                competency_id="experience",
                text="Obsolete probe",
                priority=100,
            ),
        ],
    )
    service.retire_adaptive_candidate(
        interview_id,
        "retired",
        status=QuestionStatus.COVERED_ELSEWHERE,
        reason="Later base answer supplied the evidence.",
    )
    _approve_and_start(service, interview_id)

    after_first = service.answer(interview_id, "A detailed first base answer.")
    assert after_first["last_response"].endswith("First source probe")
    assert [item["text"] for item in after_first["adaptive_questions"]] == ["First source probe"]

    next_base = service.answer(interview_id, "A detailed first probe answer.")
    assert next_base["interview_phase"] == "base"
    after_second = service.answer(interview_id, "A detailed second base answer.")

    adaptive = after_second["adaptive_questions"]
    assert [item["text"] for item in adaptive] == [
        "First source probe",
        "Second source probe",
    ]
    assert [item["source_question_id"] for item in adaptive] == [first_id, second_id]
    assert all(item["source"] == QuestionSource.ADAPTIVE for item in adaptive)
    scheduled = [
        event
        for event in after_second["audit_events"]
        if event["type"] == "adaptive_questions_scheduled"
    ]
    assert [event["source_question_id"] for event in scheduled] == [first_id, second_id]
    assert all(event["selected_count"] == 1 for event in scheduled)


def test_immediate_clarification_does_not_advance_or_buffer_a_probe():
    service = InterviewService()
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built systems",
        job_details="Operate systems",
    )
    interview_id = created["interview_id"]
    started = _approve_and_start(service, interview_id)

    clarified = service.answer(interview_id, "Could you repeat that?")

    assert clarified["current_question_index"] == 0
    assert clarified["last_response"] == started["last_response"]
    assert clarified["adaptive_candidates"] == []

    technical = service.answer(interview_id, "My microphone is not working")
    assert technical["current_question_index"] == 0
    assert technical["adaptive_candidates"] == []
    assert technical["redirects_by_question"] == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "Hi, hello.",
        "A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A-A",
    ],
)
def test_non_answers_remain_on_current_question_and_create_no_evidence(utterance):
    service = InterviewService()
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built systems",
        job_details="Operate systems",
    )
    interview_id = created["interview_id"]
    started = _approve_and_start(service, interview_id)

    state = service.answer(interview_id, utterance)

    assert state["current_question_index"] == 0
    assert state["evidence"] == []
    assert state["answers"] == []
    assert state["adaptive_candidates"] == []
    assert state["question_statuses"][started["plan"]["questions"][0]["id"]] == "asked"
    assert state["audit_events"][-1]["type"] == "answer_not_accepted"


def test_candidate_can_say_they_do_not_know_and_move_to_next_question():
    service = InterviewService()
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built reliable systems",
        job_details="Operate reliable systems",
    )
    interview_id = created["interview_id"]
    started = _approve_and_start(service, interview_id)

    state = service.answer(interview_id, "I don't know.")

    assert state["current_question_index"] == 1
    assert state["evidence"] == []
    assert state["question_statuses"][started["plan"]["questions"][0]["id"]] == "skipped"
    assert any(event["type"] == "question_skipped" for event in state["audit_events"])


def test_clarification_rephrases_from_context_without_advancing_or_scoring():
    service = InterviewService()
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=0,
        job_questions=1,
        max_company_questions=0,
        max_adaptive_questions=0,
        max_total_questions=1,
    )
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built systems",
        job_details="Operate reliable systems",
        planning_controls=controls,
    )
    interview_id = created["interview_id"]
    _approve_and_start(service, interview_id)

    state = service.answer(interview_id, "What is requirement area four?")

    assert state["current_question_index"] == 0
    assert state["evidence"] == []
    assert "real example" in state["last_response"]
    assert state["audit_events"][-1]["type"] == "question_clarified"


def test_two_unusable_answers_skip_question_and_move_on_with_transition():
    service = InterviewService()
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built reliable services",
        job_details="Operate distributed systems",
    )
    interview_id = created["interview_id"]
    started = _approve_and_start(service, interview_id)
    first_id = started["plan"]["questions"][0]["id"]

    retry = service.answer(interview_id, "Uh huh.")
    moved = service.answer(interview_id, "Happy, blah, blah, blah, blah, blah.")

    assert retry["current_question_index"] == 0
    assert moved["current_question_index"] == 1
    assert moved["question_statuses"][first_id] == "skipped"
    assert moved["last_response"].startswith("No problem. Let's move on.")
    assert moved["evidence"] == []


def test_adaptive_probe_quotes_answer_and_deduplicates_same_claim_globally():
    service = InterviewService()
    controls = InterviewPlanningControls(
        introduction_questions=0,
        resume_questions=2,
        job_questions=0,
        max_company_questions=0,
        max_adaptive_questions=2,
        max_adaptive_per_base_question=1,
        max_total_questions=4,
    )
    created = service.create_buffered(
        job_title="Engineer",
        resume_text="Built systems",
        job_details="Operate systems",
        planning_controls=controls,
    )
    interview_id = created["interview_id"]
    _approve_and_start(service, interview_id)
    answer = "I chose a queue and measured a twenty percent latency reduction."

    first_probe = service.answer(interview_id, answer, _follow_up())
    service.answer(
        interview_id,
        "I owned the queue choice and verified it against the baseline.",
        _accept(),
    )
    state = service.answer(interview_id, answer, _follow_up())

    assert len(state["adaptive_questions"]) == 1
    assert first_probe["adaptive_questions"][0]["text"].endswith("?")
    assert state["adaptive_questions"][0]["provenance"]["answer_anchor"]
