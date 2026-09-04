import pytest

from agentic_interviewer.adapters import FakeUnifiedAdapter
from agentic_interviewer.domain.models import (
    ExecutionControls,
    ProviderError,
    SpeechRequest,
    TranscriptionRequest,
)


@pytest.mark.asyncio
async def test_fake_transcription_is_literal():
    adapter = FakeUnifiedAdapter()
    result = await adapter.transcribe(
        TranscriptionRequest(
            audio=b"literal candidate response",
            controls=ExecutionControls(idempotency_key="stt-1", max_audio_seconds=10),
        )
    )
    assert result.transcript.text == "literal candidate response"


@pytest.mark.asyncio
async def test_fake_synthesis_enforces_character_budget():
    adapter = FakeUnifiedAdapter()
    request = SpeechRequest(
        text="too long",
        controls=ExecutionControls(idempotency_key="tts-1", max_output_characters=3),
    )
    with pytest.raises(ProviderError) as exc:
        async for _ in adapter.synthesize(request):
            pass
    assert exc.value.code == "budget_exceeded"
