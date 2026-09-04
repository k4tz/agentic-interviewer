from __future__ import annotations

from dataclasses import dataclass

from agentic_interviewer.adapters import (
    FakeUnifiedAdapter,
    OpenAICompatibleAudioConfig,
    OpenAICompatibleReasoningAdapter,
    OpenAICompatibleReasoningConfig,
    OpenAICompatibleSpeechSynthesisAdapter,
    OpenAICompatibleTranscriptionAdapter,
)
from agentic_interviewer.adapters.router import CapabilityRouter, ProviderBundle
from agentic_interviewer.adapters.speaches import (
    SpeachesConfig,
    SpeachesSpeechSynthesisAdapter,
    SpeachesTranscriptionAdapter,
)
from agentic_interviewer.adapters.speaches_realtime import (
    SpeachesRealtimeAdapter,
    SpeachesRealtimeConfig,
)
from agentic_interviewer.config import Settings
from agentic_interviewer.domain.realtime import RealtimeTranscriptionPort


@dataclass
class ProviderRuntime:
    router: CapabilityRouter
    probes: tuple[object, ...] = ()
    closeables: tuple[object, ...] = ()
    realtime: RealtimeTranscriptionPort | None = None

    async def health(self) -> list[dict]:
        results: list[dict] = []
        for provider in self.probes:
            result = await provider.probe()  # type: ignore[attr-defined]
            results.append(
                {
                    "provider": result.provider,
                    "healthy": result.healthy,
                    "models": sorted(result.models),
                    "missing_models": sorted(result.missing_models),
                    "detail": result.detail,
                }
            )
        return results

    async def aclose(self) -> None:
        for provider in self.closeables:
            await provider.aclose()  # type: ignore[attr-defined]


def build_provider_runtime(settings: Settings) -> ProviderRuntime:
    if settings.provider_profile == "fake":
        fake = FakeUnifiedAdapter()
        return ProviderRuntime(
            router=CapabilityRouter(
                {"fake": ProviderBundle(fake, fake, fake)}, default_profile="fake"
            )
        )
    if settings.provider_profile not in {"local-specialized", "openai-compatible"}:
        raise ValueError(f"unsupported provider profile: {settings.provider_profile}")
    reasoning_config = OpenAICompatibleReasoningConfig(
        provider=settings.reasoning_provider_name,
        base_url=settings.reasoning_base_url,
        api_key=settings.reasoning_api_key,
        region=settings.reasoning_region,
        external_processing=settings.reasoning_external_processing,
        retains_provider_data=settings.reasoning_retains_provider_data,
        reasoning_model=settings.reasoning_model,
        extra_body=(
            {"chat_template_kwargs": {"enable_thinking": settings.reasoning_enable_thinking}}
            if settings.reasoning_enable_thinking is not None
            else {}
        ),
        check_health_endpoint=settings.reasoning_check_health_endpoint,
        max_completion_tokens=settings.reasoning_max_completion_tokens,
        temperature=settings.reasoning_temperature,
        top_p=settings.reasoning_top_p,
        presence_penalty=settings.reasoning_presence_penalty,
    )
    if settings.provider_profile == "local-specialized":
        audio_config = SpeachesConfig(
            base_url=settings.speaches_base_url,
            api_key=settings.speaches_api_key,
            transcription_model=settings.speaches_stt_model,
            synthesis_model=settings.speaches_tts_model,
            default_voice=settings.speaches_default_voice,
            region=settings.speaches_region,
            external_processing=settings.speaches_external_processing,
            retains_provider_data=settings.speaches_retains_provider_data,
            check_health_endpoint=settings.speaches_check_health_endpoint,
        )
    else:
        audio_config = OpenAICompatibleAudioConfig(
            provider=settings.audio_provider_name,
            base_url=settings.audio_base_url,
            api_key=settings.audio_api_key,
            transcription_model=settings.audio_stt_model,
            synthesis_model=settings.audio_tts_model,
            default_voice=settings.audio_default_voice,
            region=settings.audio_region,
            external_processing=settings.audio_external_processing,
            retains_provider_data=settings.audio_retains_provider_data,
            check_health_endpoint=settings.audio_check_health_endpoint,
        )
    realtime = None
    speech_policy_allows_processing = (
        (not audio_config.external_processing or settings.allow_external_model_processing)
        and (not audio_config.retains_provider_data or settings.allow_provider_data_retention)
        and (
            not settings.allowed_provider_regions
            or audio_config.region in settings.allowed_provider_regions
        )
    )
    if settings.provider_profile == "local-specialized" and speech_policy_allows_processing:
        realtime = SpeachesRealtimeAdapter(
            SpeachesRealtimeConfig(
                base_url=audio_config.base_url,
                model=audio_config.transcription_model,
                api_key=audio_config.api_key,
                connect_timeout_seconds=settings.realtime_connect_timeout_seconds,
                max_message_bytes=settings.realtime_max_message_bytes,
                vad_threshold=settings.realtime_vad_threshold,
                silence_duration_ms=settings.realtime_silence_duration_ms,
            )
        )
    # Validate every configuration before acquiring HTTP clients, so invalid
    # profile settings cannot leak a partially constructed runtime.
    reasoning = OpenAICompatibleReasoningAdapter(reasoning_config)
    if settings.provider_profile == "local-specialized":
        transcription = SpeachesTranscriptionAdapter(audio_config)
        synthesis = SpeachesSpeechSynthesisAdapter(audio_config)
    else:
        transcription = OpenAICompatibleTranscriptionAdapter(audio_config)
        synthesis = OpenAICompatibleSpeechSynthesisAdapter(audio_config)
    return ProviderRuntime(
        router=CapabilityRouter(
            {
                settings.provider_profile: ProviderBundle(
                    reasoning=reasoning,
                    transcription=transcription,
                    synthesis=synthesis,
                )
            },
            default_profile=settings.provider_profile,
        ),
        probes=(reasoning, transcription, synthesis),
        closeables=(reasoning, transcription, synthesis),
        realtime=realtime,
    )
