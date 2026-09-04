import pytest

from agentic_interviewer.domain.models import (
    NormalizedUsage,
    ProviderCapabilities,
    ReasoningResult,
)
from agentic_interviewer.services import InterviewService
from agentic_interviewer.services.turn_analysis import analyze_candidate_answer


class AnalysisReasoning:
    capabilities = ProviderCapabilities(
        provider="analysis-test", model="test", structured_output=True
    )

    def __init__(self, value: dict):
        self.value = value

    async def generate(self, request):
        assert request.task == "analyze_answer"
        assert request.controls.max_output_tokens == 350
        assert "confidence is a JSON number from 0.0 through 1.0" in request.prompt
        assert '"confidence":0.9' in request.prompt
        return ReasoningResult(
            value=self.value,
            usage=NormalizedUsage(provider="analysis-test", model="test", capability="reasoning"),
        )


@pytest.mark.asyncio
async def test_semantic_analysis_can_request_one_targeted_follow_up():
    service = InterviewService()
    state = service.create(
        job_title="Engineer",
        resume_text="Built reliable APIs",
        job_details="Operate distributed systems",
    )
    service.approve_setup(state["interview_id"], "reviewer", "verified")
    state = service.start(state["interview_id"])

    analysis, usage = await analyze_candidate_answer(
        interview_id=state["interview_id"],
        state=state,
        answer="I designed and launched an asynchronous ingestion service.",
        reasoning=AnalysisReasoning(
            {
                "disposition": "follow_up",
                "confidence": 0.93,
                "feedback": "",
                "follow_up_question": "How did you measure its production reliability?",
            }
        ),
        allow_external_processing=False,
        allowed_regions=set(),
        retain_provider_data=False,
    )

    assert analysis is not None
    assert analysis.disposition == "follow_up"
    assert analysis.follow_up_question == "How did you measure its production reliability?"
    assert usage is not None


@pytest.mark.asyncio
async def test_deterministic_skip_does_not_call_reasoning():
    service = InterviewService()
    state = service.create(
        job_title="Engineer",
        resume_text="Built reliable APIs",
        job_details="Operate distributed systems",
    )
    service.approve_setup(state["interview_id"], "reviewer", "verified")
    state = service.start(state["interview_id"])

    analysis, usage = await analyze_candidate_answer(
        interview_id=state["interview_id"],
        state=state,
        answer="I don't know.",
        reasoning=AnalysisReasoning({}),
        allow_external_processing=False,
        allowed_regions=set(),
        retain_provider_data=False,
    )

    assert analysis is None
    assert usage is None
