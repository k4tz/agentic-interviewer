import pytest
from pydantic import ValidationError

from agentic_interviewer.domain.models import (
    ExecutionControls,
    ProviderCapabilities,
    ResumeProfile,
    SourceClaim,
    TextSpan,
)


def test_resume_claim_preserves_source_span():
    profile = ResumeProfile(
        id="resume-1",
        candidate_id="candidate-1",
        claims=[
            SourceClaim(
                id="claim-1",
                kind="experience",
                value="Built an ingestion service",
                source_document="resume.pdf",
                span=TextSpan(start=10, end=36, text="Built an ingestion service"),
            )
        ],
    )
    assert profile.claims[0].source_document == "resume.pdf"


def test_execution_controls_require_positive_deadline():
    with pytest.raises(ValidationError):
        ExecutionControls(idempotency_key="test", deadline_ms=0)


def test_live_input_and_partial_transcripts_are_independent_capabilities():
    capabilities = ProviderCapabilities(
        provider="speaches",
        model="speech",
        live_audio_input=True,
        partial_transcripts=False,
    )

    assert capabilities.live_audio_input is True
    assert capabilities.partial_transcripts is False
    assert capabilities.streaming_stt is False


def test_compatibility_views_derive_from_the_explicit_audio_capabilities():
    capabilities = ProviderCapabilities(
        provider="speaches",
        model="speech",
        live_audio_input=True,
        partial_transcripts=True,
        streaming_audio_output=True,
        supported_input_audio_formats={"wav", "webm"},
        supported_output_audio_formats={"wav", "mp3"},
    )

    assert capabilities.streaming_stt is True
    assert capabilities.streaming_tts is True
    assert capabilities.supported_audio_formats == {"wav", "webm", "mp3"}
