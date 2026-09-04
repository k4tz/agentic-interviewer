"""Application-owned live transcription contract; no provider wire details.

Audio is base64-encoded mono PCM16 at 24 kHz. Credentials, provider session
configuration, transport event names and raw failures never cross this seam.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class RealtimeAudioCommand:
    kind: Literal["append", "commit", "clear"]
    audio: str | None = None


@dataclass(frozen=True)
class RealtimeTranscriptEvent:
    kind: Literal["speech_started", "speech_stopped", "transcript_delta", "transcript_completed"]
    text: str = ""


class RealtimeError(RuntimeError):
    """A bounded failure code; exception text is never candidate-facing."""

    def __init__(self, code: str = "voice_unavailable") -> None:
        super().__init__(code)
        self.code = code


class RealtimeTranscriptionSession(Protocol):
    async def send(self, command: RealtimeAudioCommand) -> None: ...

    def events(self) -> AsyncIterator[RealtimeTranscriptEvent]: ...


class RealtimeTranscriptionPort(Protocol):
    def open_session(
        self, language: str
    ) -> AbstractAsyncContextManager[RealtimeTranscriptionSession]: ...
