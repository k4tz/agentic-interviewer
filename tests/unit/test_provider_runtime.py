import httpx
import pytest

from agentic_interviewer.adapters import (
    OpenAICompatibleReasoningAdapter,
    OpenAICompatibleSpeechSynthesisAdapter,
    OpenAICompatibleTranscriptionAdapter,
)
from agentic_interviewer.adapters.speaches import (
    SpeachesSpeechSynthesisAdapter,
    SpeachesTranscriptionAdapter,
)
from agentic_interviewer.composition.providers import build_provider_runtime
from agentic_interviewer.config import Settings
from agentic_interviewer.domain.models import ExecutionControls, ProviderError, ReasoningRequest


@pytest.mark.asyncio
async def test_local_specialized_runtime_routes_reasoning_and_speech_to_separate_ports():
    runtime = build_provider_runtime(
        Settings(
            _env_file=None,
            provider_profile="local-specialized",
            reasoning_base_url="http://reasoning.test",
            reasoning_model="reasoning-local",
            reasoning_provider_name="local-reasoning",
            reasoning_region="local",
            reasoning_external_processing=False,
            reasoning_retains_provider_data=False,
            reasoning_max_completion_tokens=768,
            reasoning_temperature=0.2,
            reasoning_top_p=0.85,
            reasoning_presence_penalty=0.1,
            speaches_stt_model="whisper-local",
            speaches_tts_model="kokoro-local",
        )
    )
    try:
        bundle = runtime.router.bundle()

        assert isinstance(bundle.reasoning, OpenAICompatibleReasoningAdapter)
        assert not hasattr(bundle.reasoning, "transcribe")
        assert isinstance(bundle.transcription, SpeachesTranscriptionAdapter)
        assert isinstance(bundle.synthesis, SpeachesSpeechSynthesisAdapter)
        assert bundle.transcription is not bundle.synthesis
        assert bundle.reasoning.capabilities.model == "reasoning-local"
        assert bundle.reasoning.capabilities.provider == "local-reasoning"
        assert bundle.reasoning.config.max_completion_tokens == 768
        assert bundle.reasoning.config.temperature == 0.2
        assert bundle.reasoning.config.top_p == 0.85
        assert bundle.reasoning.config.presence_penalty == 0.1
        assert bundle.transcription.capabilities.provider == "speaches"
        assert bundle.transcription.capabilities.model == "whisper-local"
        assert bundle.synthesis.capabilities.provider == "speaches"
        assert bundle.synthesis.capabilities.model == "kokoro-local"
        assert runtime.realtime is not None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_hosted_audio_profile_preserves_policy_and_provider_provenance():
    runtime = build_provider_runtime(
        Settings(
            _env_file=None,
            provider_profile="openai-compatible",
            reasoning_base_url="http://reasoning.test",
            reasoning_model="reasoning-test",
            audio_provider_name="hosted-speech",
            audio_base_url="https://speech.example.test",
            audio_stt_model="hosted-stt",
            audio_tts_model="hosted-tts",
            audio_default_voice="hosted-voice",
            audio_region="in",
            audio_external_processing=True,
            audio_retains_provider_data=True,
            audio_check_health_endpoint=False,
        )
    )
    try:
        bundle = runtime.router.bundle()

        assert bundle.transcription.capabilities.provider == "hosted-speech"
        assert bundle.transcription.capabilities.model == "hosted-stt"
        assert bundle.transcription.capabilities.external_processing is True
        assert bundle.transcription.capabilities.region == "in"
        assert bundle.synthesis.capabilities.model == "hosted-tts"
        assert bundle.transcription.config.retains_provider_data is True
        assert bundle.transcription.config.check_health_endpoint is False
        assert bundle.synthesis.config.default_voice == "hosted-voice"
        assert type(bundle.transcription) is OpenAICompatibleTranscriptionAdapter
        assert type(bundle.synthesis) is OpenAICompatibleSpeechSynthesisAdapter
        assert runtime.realtime is None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"speaches_external_processing": True},
        {"speaches_retains_provider_data": True},
        {"allowed_provider_regions": {"elsewhere"}},
    ],
)
async def test_realtime_is_unavailable_when_speech_policy_denies_processing(overrides):
    runtime = build_provider_runtime(
        Settings(
            _env_file=None,
            provider_profile="local-specialized",
            reasoning_base_url="http://reasoning.test",
            reasoning_model="reasoning-test",
            **overrides,
        )
    )
    try:
        assert runtime.realtime is None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_fake_runtime_has_no_live_transcription_provider():
    runtime = build_provider_runtime(Settings(_env_file=None, provider_profile="fake"))
    assert runtime.realtime is None
    await runtime.aclose()


def test_live_profiles_require_explicit_reasoning_model():
    with pytest.raises(ValueError, match="must not be empty"):
        build_provider_runtime(
            Settings(
                _env_file=None,
                provider_profile="local-specialized",
                reasoning_base_url="http://reasoning.test",
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        {"reasoning_external_processing": True, "reasoning_retains_provider_data": False},
        {"reasoning_external_processing": False, "reasoning_retains_provider_data": True},
    ],
)
async def test_reasoning_runtime_carries_privacy_and_rejects_unpermitted_calls(policy):
    runtime = build_provider_runtime(
        Settings(
            _env_file=None,
            provider_profile="local-specialized",
            reasoning_base_url="https://reasoning.test",
            reasoning_model="reasoning-test",
            reasoning_region="region-test",
            **policy,
        )
    )
    try:
        reasoning = runtime.router.bundle().reasoning
        assert reasoning.capabilities.region == "region-test"
        assert reasoning.capabilities.external_processing == policy["reasoning_external_processing"]
        assert reasoning.config.retains_provider_data == policy["reasoning_retains_provider_data"]
        await reasoning.aclose()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: pytest.fail("unexpected provider request")
            )
        ) as client:
            reasoning._client = client
            with pytest.raises(ProviderError) as exc:
                await reasoning.generate(
                    ReasoningRequest(
                        task="classify_intent",
                        prompt="Classify",
                        controls=ExecutionControls(idempotency_key="privacy-test"),
                    )
                )
            assert exc.value.code == "policy_denied"
    finally:
        await runtime.aclose()
