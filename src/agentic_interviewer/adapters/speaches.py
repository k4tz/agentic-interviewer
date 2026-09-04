"""Speaches deployment defaults and buffered OpenAI-compatible audio adapters.

Only this provider boundary owns its model names, default voice, and health
endpoint assumptions. Application services consume the neutral audio ports.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_interviewer.adapters.openai_compatible import (
    OpenAICompatibleAudioConfig,
    OpenAICompatibleSpeechSynthesisAdapter,
    OpenAICompatibleTranscriptionAdapter,
)


@dataclass(frozen=True)
class SpeachesConfig(OpenAICompatibleAudioConfig):
    base_url: str = "http://127.0.0.1:8001"
    transcription_model: str = "Systran/faster-distil-whisper-small.en"
    synthesis_model: str = "speaches-ai/Kokoro-82M-v1.0-ONNX"
    provider: str = "speaches"
    region: str = "local"
    external_processing: bool = False
    retains_provider_data: bool = False
    default_voice: str | None = "af_heart"
    check_health_endpoint: bool = True
    # Compatibility with the former TTS-only configuration lives here, not in
    # the provider-neutral protocol implementation.
    model: str | None = None

    def __post_init__(self) -> None:
        if self.model is not None:
            object.__setattr__(self, "synthesis_model", self.model)
        super().__post_init__()


class SpeachesTranscriptionAdapter(OpenAICompatibleTranscriptionAdapter):
    """Buffered Speaches transcription; no realtime capability is implied."""


class SpeachesSpeechSynthesisAdapter(OpenAICompatibleSpeechSynthesisAdapter):
    """Buffered Speaches synthesis with a provider-owned default voice."""


# Legacy TTS-only import remains available solely in the provider module.
SpeachesKokoroAdapter = SpeachesSpeechSynthesisAdapter
