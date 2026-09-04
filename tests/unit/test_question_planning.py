from __future__ import annotations

import pytest

from agentic_interviewer.domain.models import (
    NormalizedUsage,
    ProviderCapabilities,
    ReasoningResult,
)
from agentic_interviewer.services import InterviewService
from agentic_interviewer.services.candidate_intake import prepare_question_bank
from agentic_interviewer.services.interviews import InterviewConflictError
from agentic_interviewer.services.question_planning import (
    GroundedQuestionBank,
    PlannedQuestion,
    QuestionQualityError,
    build_grounded_fallback_bank,
    extract_job_requirements,
    extract_resume_claims,
    validate_question_bank,
)

RESUME = (
    "Candidate Profile\n"
    "Designed a queue-backed document pipeline that cut processing time by 40 percent.\n"
    "Led an API reliability project and added production tracing."
)
JOB = (
    "Build reliable application services.\n\nRequirements:\n"
    "- Python API design and production operations\n"
    "- Queue-based processing with observable failure handling"
)


class StubReasoning:
    capabilities = ProviderCapabilities(
        provider="stub",
        model="offline",
        structured_output=True,
        live_audio_input=False,
        partial_transcripts=False,
        streaming_audio_output=False,
        verbatim_tts=True,
    )

    def __init__(self, value: dict) -> None:
        self.value = value
        self.request = None

    async def generate(self, request):
        self.request = request
        return ReasoningResult(
            value=self.value,
            usage=NormalizedUsage(
                provider="stub",
                model="offline",
                capability="reasoning",
            ),
        )


def test_extracts_source_items_with_provenance_and_skips_name_line():
    resume_items = extract_resume_claims(RESUME)
    job_items = extract_job_requirements(JOB)

    assert [item.id for item in resume_items] == ["resume-claim-1", "resume-claim-2"]
    assert "Candidate Profile" not in {item.text for item in resume_items}
    assert resume_items[0].line == 2
    assert RESUME[resume_items[0].start : resume_items[0].end] == resume_items[0].text
    assert [item.line for item in job_items] == [4, 5]
    assert all(item.source == "job_details" for item in job_items)


def test_resume_extraction_rejects_headers_contacts_and_truncated_fragments():
    resume = (
        "Madhad Kwan\n"
        "Software Engineer | GenAI\n"
        "github.com/k4tz\n"
        "Software engineer with 3 years of\n"
        "Experience building production AI applications.\n"
        "Built RAG workflows that reduced support lookup time by 30 percent."
    )

    items = extract_resume_claims(resume)

    assert [item.text for item in items] == [
        "Experience building production AI applications.",
        "Built RAG workflows that reduced support lookup time by 30 percent.",
    ]
    assert all("github.com" not in item.text for item in items)
    assert all(not item.text.endswith(" of") for item in items)


def test_grounded_fallback_names_real_source_content_and_records_spans():
    bank, resume_items, job_items = build_grounded_fallback_bank(
        job_title="Application Engineer",
        resume_text=RESUME,
        job_details=JOB,
        expected_counts={"introduction": 1, "resume": 5, "job": 5},
    )

    combined = " ".join(question.text.casefold() for question in bank.resume + bank.job)
    assert "resume topic" not in combined
    assert "requirement area" not in combined
    assert "queue-backed document pipeline" in combined
    assert "python api design" in combined
    assert '"' not in combined
    assert "your resume states" not in combined
    assert "in the resume statement" not in combined
    assert bank.resume[0].text.startswith("You designed a queue-backed document pipeline")
    assert bank.job[0].text.startswith("This role calls for experience with Python API design")
    assert all(question.source_item_id for question in bank.resume + bank.job)
    validate_question_bank(
        bank,
        resume_items=resume_items,
        job_items=job_items,
        expected_counts={"introduction": 1, "resume": 5, "job": 5},
    )


def test_quality_gate_rejects_placeholder_ungrounded_and_duplicate_questions():
    resume_items = extract_resume_claims(RESUME)
    bank = GroundedQuestionBank(
        resume=[
            PlannedQuestion(
                text="Choose a relevant resume claim and explain what happened?",
                source_item_id="missing",
            ),
            PlannedQuestion(
                text="Choose a relevant resume claim and explain what happened?",
                source_item_id="missing",
            ),
        ]
    )

    with pytest.raises(QuestionQualityError) as raised:
        validate_question_bank(
            bank,
            resume_items=resume_items,
            job_items=[],
            expected_counts={"introduction": 0, "resume": 2, "job": 0},
        )

    message = str(raised.value)
    assert "placeholder wording" in message
    assert "valid resume source item" in message
    assert "duplicates" in message


def test_quality_gate_rejects_raw_source_quotes_and_resume_fragment_wrappers():
    resume_items = extract_resume_claims(RESUME)
    source = resume_items[0]
    bank = GroundedQuestionBank(
        resume=[
            PlannedQuestion(
                text=f'Your resume states, "{source.text}" What did you personally own?',
                source_item_id=source.id,
            )
        ]
    )

    with pytest.raises(QuestionQualityError, match="quoted fragment"):
        validate_question_bank(
            bank,
            resume_items=resume_items,
            job_items=[],
            expected_counts={"introduction": 0, "resume": 1, "job": 0},
        )


@pytest.mark.parametrize(
    "text",
    [
        "Tell me about your engineering background and what draws you to this role.",
        "Please briefly introduce yourself and summarize your relevant engineering background.",
        (
            "Your background includes production APIs. Describe a difficult reliability issue "
            "you diagnosed and the evidence that confirmed the fix."
        ),
    ],
)
def test_quality_gate_accepts_open_ended_imperative_prompt(text: str):
    bank = GroundedQuestionBank(introduction=[PlannedQuestion(text=text)])

    validate_question_bank(
        bank,
        resume_items=[],
        job_items=[],
        expected_counts={"introduction": 1, "resume": 0, "job": 0},
    )


@pytest.mark.asyncio
async def test_provider_questions_must_be_source_linked_and_provenance_is_preserved():
    service = InterviewService()
    state = service.create_buffered(
        job_title="Application Engineer",
        resume_text=RESUME,
        job_details=JOB,
        planning_controls={
            "introduction_questions": 1,
            "resume_questions": 1,
            "job_questions": 1,
            "max_company_questions": 0,
            "max_adaptive_questions": 0,
            "max_total_questions": 3,
        },
    )
    reasoning = StubReasoning(
        {
            "introduction": [
                {
                    "text": "Why is the Application Engineer role the right next step for you?",
                    "source_item_id": None,
                }
            ],
            "resume": [
                {
                    "text": (
                        "You designed a queue-backed document pipeline; what did you personally "
                        "change to reduce processing time?"
                    ),
                    "source_item_id": "resume-claim-1",
                }
            ],
            "job": [
                {
                    "text": (
                        "For Python API design and production operations, which reliability "
                        "trade-off would you evaluate first?"
                    ),
                    "source_item_id": "job-requirement-1",
                }
            ],
        }
    )

    updated, _usage = await prepare_question_bank(
        interview_id=state["interview_id"],
        state=state,
        resume_text=RESUME,
        job_details=JOB,
        reasoning=reasoning,
        service=service,
        allow_external_processing=False,
        allowed_regions=set(),
        retain_provider_data=False,
    )

    questions = updated["plan"]["questions"]
    grounded = [question for question in questions if question["source"] != "introduction"]
    assert [question["provenance"]["source_item_id"] for question in grounded] == [
        "resume-claim-1",
        "job-requirement-1",
    ]
    assert all(question["provenance"]["source_span"]["end"] > 0 for question in grounded)
    assert "resume" not in reasoning.request.context
    assert "job_details" not in reasoning.request.context
    assert "naturally paraphrase" in reasoning.request.prompt
    assert "Never quote a raw source excerpt" in reasoning.request.prompt
    assert "Ask one main thing at a time" in reasoning.request.prompt
    assert "Do not use Markdown, code fences" in reasoning.request.prompt
    assert "Every introduction item must use JSON null" in reasoning.request.prompt
    assert "never change hyphens to underscores" in reasoning.request.prompt
    assert "Never swap those categories" in reasoning.request.prompt


@pytest.mark.asyncio
async def test_provider_placeholder_question_fails_closed():
    service = InterviewService()
    state = service.create_buffered(
        job_title="Application Engineer",
        resume_text=RESUME,
        job_details=JOB,
        planning_controls={
            "introduction_questions": 0,
            "resume_questions": 1,
            "job_questions": 0,
            "max_company_questions": 0,
            "max_adaptive_questions": 0,
            "max_total_questions": 1,
        },
    )
    reasoning = StubReasoning(
        {
            "introduction": [],
            "resume": [
                {
                    "text": "Choose a relevant resume claim and explain your decisions?",
                    "source_item_id": "resume-claim-1",
                }
            ],
            "job": [],
        }
    )

    with pytest.raises(InterviewConflictError, match="placeholder wording"):
        await prepare_question_bank(
            interview_id=state["interview_id"],
            state=state,
            resume_text=RESUME,
            job_details=JOB,
            reasoning=reasoning,
            service=service,
            allow_external_processing=False,
            allowed_regions=set(),
            retain_provider_data=False,
        )
