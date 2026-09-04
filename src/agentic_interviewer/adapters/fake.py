from collections.abc import AsyncIterator

from agentic_interviewer.adapters.router import CapabilityRouter
from agentic_interviewer.domain.models import (
    AudioEvent,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderError,
    ReasoningRequest,
    ReasoningResult,
    SpeechRequest,
    Transcript,
    TranscriptionRequest,
    TranscriptionResult,
)


class FakeUnifiedAdapter:
    """Deterministic network-free adapter implementing all capability ports."""

    capabilities = ProviderCapabilities(
        provider="fake",
        model="deterministic-v1",
        structured_output=True,
        live_audio_input=False,
        partial_transcripts=False,
        streaming_audio_output=True,
        verbatim_tts=True,
    )

    async def generate(self, request: ReasoningRequest) -> ReasoningResult:
        CapabilityRouter.assert_eligible(self.capabilities, request.controls)
        value = {"task": request.task, "text": request.prompt, "context": request.context}
        return ReasoningResult(
            value=value,
            usage=NormalizedUsage(
                provider="fake",
                model="deterministic-v1",
                capability="reasoning",
                input_tokens=len(request.prompt.split()),
                output_tokens=len(request.prompt.split()),
            ),
        )

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        CapabilityRouter.assert_eligible(self.capabilities, request.controls)
        duration = len(request.audio) / 32_000
        if request.controls.max_audio_seconds is not None:
            if duration > request.controls.max_audio_seconds:
                raise ProviderError("budget_exceeded", "audio duration exceeds request limit")
        text = request.audio.decode("utf-8", errors="replace")
        return TranscriptionResult(
            transcript=Transcript(text=text, duration_ms=int(duration * 1000)),
            usage=NormalizedUsage(
                provider="fake",
                model="deterministic-v1",
                capability="transcription",
                input_audio_seconds=duration,
            ),
        )

    async def synthesize(self, request: SpeechRequest) -> AsyncIterator[AudioEvent]:
        CapabilityRouter.assert_eligible(self.capabilities, request.controls)
        limit = request.controls.max_output_characters
        if limit is not None and len(request.text) > limit:
            raise ProviderError("budget_exceeded", "text exceeds synthesis limit")
        yield AudioEvent(
            audio=request.text.encode(),
            usage=NormalizedUsage(
                provider="fake",
                model="deterministic-v1",
                capability="synthesis",
                synthesized_characters=len(request.text),
            ),
        )
