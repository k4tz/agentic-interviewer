from dataclasses import dataclass, field
from decimal import Decimal

from agentic_interviewer.domain.models import (
    BudgetState,
    ExecutionControls,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderError,
)
from agentic_interviewer.domain.ports import ReasoningPort, SpeechSynthesisPort, TranscriptionPort


@dataclass(frozen=True)
class ProviderBundle:
    reasoning: ReasoningPort
    transcription: TranscriptionPort
    synthesis: SpeechSynthesisPort


class CapabilityRouter:
    def __init__(self, profiles: dict[str, ProviderBundle], default_profile: str) -> None:
        if default_profile not in profiles:
            raise ValueError("default provider profile is not registered")
        self._profiles = profiles
        self._default = default_profile

    def bundle(self, profile: str | None = None) -> ProviderBundle:
        try:
            return self._profiles[profile or self._default]
        except KeyError as exc:
            raise ProviderError(
                "provider_unavailable", "provider profile is not registered"
            ) from exc

    @staticmethod
    def assert_eligible(capabilities: ProviderCapabilities, controls: ExecutionControls) -> None:
        if controls.require_structured_output and not capabilities.structured_output:
            raise ProviderError("unsupported_capability", "structured output is required")
        if controls.require_verbatim_output and not capabilities.verbatim_tts:
            raise ProviderError("unsupported_capability", "verbatim synthesis is required")
        requirements = (
            ("require_live_audio_input", "live_audio_input"),
            ("require_partial_transcripts", "partial_transcripts"),
            ("require_server_vad", "server_vad"),
            ("require_manual_commit", "manual_commit"),
            ("require_streaming_audio_output", "streaming_audio_output"),
            ("require_barge_in", "barge_in"),
            ("require_word_timestamps", "word_timestamps"),
            ("require_segment_timestamps", "segment_timestamps"),
        )
        for control_name, capability_name in requirements:
            if getattr(controls, control_name) and not getattr(capabilities, capability_name):
                raise ProviderError(
                    "unsupported_capability",
                    f"{capability_name.replace('_', ' ')} is required",
                )
        if not controls.allow_external_processing and capabilities.external_processing:
            raise ProviderError("policy_denied", "external processing is not allowed")
        if controls.allowed_regions and capabilities.region not in controls.allowed_regions:
            raise ProviderError("policy_denied", "provider region is not allowed")


@dataclass
class BudgetLedger:
    """Accumulates normalized usage without exposing provider wire data."""

    state: BudgetState = field(default_factory=BudgetState)

    def record(self, usage: NormalizedUsage) -> BudgetState:
        self.state.input_tokens += usage.input_tokens or 0
        self.state.output_tokens += usage.output_tokens or 0
        self.state.input_audio_seconds += usage.input_audio_seconds or 0
        self.state.synthesized_characters += usage.synthesized_characters or 0
        self.state.estimated_cost_usd += Decimal(usage.estimated_cost_usd)
        return self.state.model_copy(deep=True)
