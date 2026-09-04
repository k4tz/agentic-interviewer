from collections.abc import AsyncIterator
from typing import Protocol

from agentic_interviewer.domain.models import (
    AudioEvent,
    ProviderCapabilities,
    ReasoningRequest,
    ReasoningResult,
    SpeechRequest,
    TranscriptionRequest,
    TranscriptionResult,
)


class ReasoningPort(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def generate(self, request: ReasoningRequest) -> ReasoningResult: ...


class TranscriptionPort(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...


class SpeechSynthesisPort(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def synthesize(self, request: SpeechRequest) -> AsyncIterator[AudioEvent]: ...
