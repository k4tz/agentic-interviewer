import io
import json
import wave

import httpx
import pytest

from agentic_interviewer.adapters.openai_compatible import (
    OpenAICompatibleAudioConfig,
    OpenAICompatibleReasoningAdapter,
    OpenAICompatibleReasoningConfig,
    OpenAICompatibleSpeechSynthesisAdapter,
    OpenAICompatibleTranscriptionAdapter,
)
from agentic_interviewer.adapters.speaches import (
    SpeachesConfig,
    SpeachesKokoroAdapter,
)
from agentic_interviewer.domain.models import (
    ExecutionControls,
    ProviderError,
    ReasoningRequest,
    SpeechRequest,
    TranscriptionRequest,
)


def controls(**overrides):
    return ExecutionControls(idempotency_key="wire-test", **overrides)


def reasoning_config(**overrides):
    return OpenAICompatibleReasoningConfig(
        **{
            "base_url": "http://provider.test",
            "reasoning_model": "reasoning-test",
            "region": "local",
            "external_processing": False,
            "retains_provider_data": False,
            **overrides,
        }
    )


def audio_config(**overrides):
    return OpenAICompatibleAudioConfig(
        **{
            "base_url": "http://provider.test",
            "transcription_model": "transcription-test",
            "synthesis_model": "synthesis-test",
            "default_voice": "voice-test",
            "region": "local",
            "external_processing": False,
            "retains_provider_data": False,
            **overrides,
        }
    )


def wav_bytes(seconds: float = 0.1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\0\0" * int(16_000 * seconds))
    return output.getvalue()


def async_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://provider.test", transport=httpx.MockTransport(handler)
    )


def test_adapter_configuration_rejects_invalid_values():
    with pytest.raises(ValueError, match="http or https"):
        reasoning_config(base_url="file:///tmp/socket")
    with pytest.raises(ValueError, match="positive"):
        SpeachesConfig(probe_timeout_seconds=0)
    with pytest.raises(ValueError, match="must not be empty"):
        SpeachesConfig(model=" ")


def test_generic_configs_require_endpoints_and_models_without_provider_defaults():
    with pytest.raises(TypeError):
        OpenAICompatibleReasoningConfig()
    with pytest.raises(TypeError):
        OpenAICompatibleAudioConfig()
    assert reasoning_config().provider == "openai-compatible"
    assert audio_config().provider == "openai-compatible"


def test_generic_configs_default_to_conservative_hosted_privacy():
    config = OpenAICompatibleReasoningConfig(
        base_url="https://provider.test", reasoning_model="explicit-model"
    )
    assert config.region == "vendor"
    assert config.external_processing is True
    assert config.retains_provider_data is True


@pytest.mark.parametrize("key", ["model", "messages", "max_tokens", "response_format", "stream"])
def test_reasoning_extensions_cannot_override_controlled_fields(key):
    with pytest.raises(ValueError, match="controlled request fields"):
        reasoning_config(extra_body={key: "override"})


def test_adapter_configs_do_not_expose_credentials_in_repr():
    assert "private-token" not in repr(reasoning_config(api_key="private-token"))
    assert "private-token" not in repr(audio_config(api_key="private-token"))
    assert "private-token" not in repr(SpeachesConfig(api_key="private-token"))


@pytest.mark.asyncio
async def test_generic_probe_does_not_assume_health_endpoint():
    paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "reasoning-test"}]})

    adapter = OpenAICompatibleReasoningAdapter(reasoning_config(), client=async_client(handler))
    assert (await adapter.probe()).healthy is True
    assert paths == ["/v1/models"]


def test_reasoning_adapter_exposes_reasoning_only():
    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(),
        client=async_client(lambda request: httpx.Response(500)),
    )

    assert callable(adapter.generate)
    assert not hasattr(adapter, "transcribe")
    assert adapter.capabilities.supported_input_audio_formats == set()
    assert adapter.capabilities.supported_output_audio_formats == set()


@pytest.mark.asyncio
async def test_reasoning_wire_contract_and_normalized_usage():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Idempotency-Key"] == "wire-test"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 20
        assert payload["temperature"] == 0.3
        assert payload["top_p"] == 0.9
        assert payload["presence_penalty"] == 0.0
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"score": 3}'}}],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 4,
                    "prompt_tokens_details": {"cached_tokens": 2},
                },
            },
        )

    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(
            reasoning_model="reasoning-local",
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        ),
        client=async_client(handler),
    )
    result = await adapter.generate(
        ReasoningRequest(
            task="score",
            prompt="Score only cited evidence",
            context={"rubric": "approved-v1"},
            controls=controls(max_output_tokens=20, require_structured_output=True),
        )
    )
    assert result.value == {"score": 3}
    assert result.usage.model == "reasoning-local"
    assert result.usage.provider == "openai-compatible"
    assert result.usage.input_tokens == 9
    assert result.usage.output_tokens == 4
    assert result.usage.cached_input_tokens == 2
    assert result.usage.usage_source == "provider"


@pytest.mark.asyncio
async def test_reasoning_accepts_one_complete_json_markdown_fence():
    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(),
        client=async_client(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '```json\n{"score": 3}\n```'}}],
                    "usage": {},
                },
            )
        ),
    )

    result = await adapter.generate(
        ReasoningRequest(
            task="score",
            prompt="Return JSON",
            controls=controls(require_structured_output=True),
        )
    )

    assert result.value == {"score": 3}


@pytest.mark.asyncio
async def test_reasoning_rejects_fenced_json_with_trailing_commentary():
    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(),
        client=async_client(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": '```json\n{"score": 3}\n```\nDone'}}]},
            )
        ),
    )

    with pytest.raises(ProviderError) as exc:
        await adapter.generate(
            ReasoningRequest(
                task="score",
                prompt="Return JSON",
                controls=controls(require_structured_output=True),
            )
        )

    assert exc.value.code == "invalid_provider_response"


@pytest.mark.asyncio
async def test_reasoning_caps_request_output_at_configured_completion_limit():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 64
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"question": "Why?"}'}}],
                "usage": {},
            },
        )

    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(max_completion_tokens=64),
        client=async_client(handler),
    )
    await adapter.generate(
        ReasoningRequest(
            task="render_question",
            prompt="Render one question",
            controls=controls(max_output_tokens=2_500),
        )
    )


@pytest.mark.asyncio
async def test_reasoning_rejects_empty_final_content_with_normalized_reason():
    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(),
        client=async_client(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "", "reasoning_content": "hidden"},
                        }
                    ]
                },
            )
        ),
    )

    with pytest.raises(ProviderError) as exc:
        await adapter.generate(
            ReasoningRequest(
                task="prepare_question_bank",
                prompt="Return JSON",
                controls=controls(require_structured_output=True),
            )
        )

    assert exc.value.code == "empty_provider_response"
    assert "finish_reason=length" in str(exc.value)


@pytest.mark.asyncio
async def test_reasoning_rejects_unenforceable_input_token_control_without_http_call():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    adapter = OpenAICompatibleReasoningAdapter(reasoning_config(), client=async_client(handler))
    with pytest.raises(ProviderError) as exc:
        await adapter.generate(
            ReasoningRequest(
                task="render_question",
                prompt="Question",
                controls=controls(max_input_tokens=10),
            )
        )
    assert exc.value.code == "unsupported_control"
    assert called is False


@pytest.mark.asyncio
async def test_openai_compatible_transcription_enforces_duration_before_http():
    adapter = OpenAICompatibleTranscriptionAdapter(
        audio_config(),
        client=async_client(lambda request: httpx.Response(200, json={"text": "literal answer"})),
    )
    with pytest.raises(ProviderError) as exc:
        await adapter.transcribe(
            TranscriptionRequest(audio=wav_bytes(0.2), controls=controls(max_audio_seconds=0.1))
        )
    assert exc.value.code == "budget_exceeded"


@pytest.mark.asyncio
async def test_openai_compatible_transcription_wire_contract_and_measured_usage():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert b'name="model"' in request.content
        assert b"whisper-local" in request.content
        assert b'name="file"' in request.content
        return httpx.Response(200, json={"text": "literal answer", "language": "en"})

    adapter = OpenAICompatibleTranscriptionAdapter(
        audio_config(transcription_model="whisper-local"),
        client=async_client(handler),
    )
    result = await adapter.transcribe(
        TranscriptionRequest(audio=wav_bytes(0.1), controls=controls(max_audio_seconds=1))
    )
    assert result.transcript.text == "literal answer"
    assert result.transcript.duration_ms == 100
    assert result.usage.provider == "openai-compatible"
    assert result.usage.model == "whisper-local"
    assert result.usage.input_audio_seconds == pytest.approx(0.1)
    assert result.usage.usage_source == "measured"


@pytest.mark.asyncio
async def test_openai_compatible_synthesis_wire_contract_and_normalized_usage():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/speech"
        payload = json.loads(request.content)
        assert payload == {
            "model": "synthesis-test",
            "voice": "voice-test",
            "input": "Hello candidate",
            "response_format": "wav",
            "speed": 1.0,
        }
        return httpx.Response(200, content=b"wave-data")

    adapter = OpenAICompatibleSpeechSynthesisAdapter(audio_config(), client=async_client(handler))
    events = [
        event
        async for event in adapter.synthesize(
            SpeechRequest(
                text="Hello candidate",
                controls=controls(max_output_characters=100),
            )
        )
    ]
    assert len(events) == 1
    assert events[0].audio == b"wave-data"
    assert events[0].usage is not None
    assert events[0].usage.synthesized_characters == 15
    assert events[0].usage.provider == "openai-compatible"
    assert events[0].usage.model == "synthesis-test"


def test_batch_audio_ports_do_not_overclaim_realtime_transport_features():
    config = audio_config()
    transcription = OpenAICompatibleTranscriptionAdapter(
        config, client=async_client(lambda request: httpx.Response(500))
    )
    synthesis = OpenAICompatibleSpeechSynthesisAdapter(
        config, client=async_client(lambda request: httpx.Response(500))
    )

    assert transcription.capabilities.live_audio_input is False
    assert transcription.capabilities.partial_transcripts is False
    assert transcription.capabilities.server_vad is False
    assert transcription.capabilities.manual_commit is False
    assert transcription.capabilities.streaming_stt is False
    assert synthesis.capabilities.streaming_audio_output is False
    assert synthesis.capabilities.barge_in is False
    assert synthesis.capabilities.streaming_tts is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "required_capability",
    [
        "require_live_audio_input",
        "require_partial_transcripts",
        "require_server_vad",
        "require_manual_commit",
        "require_word_timestamps",
        "require_segment_timestamps",
    ],
)
async def test_batch_transcription_fails_closed_for_realtime_requirements(
    required_capability: str,
):
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"text": "answer"})

    adapter = OpenAICompatibleTranscriptionAdapter(audio_config(), client=async_client(handler))
    with pytest.raises(ProviderError) as exc:
        await adapter.transcribe(
            TranscriptionRequest(
                audio=wav_bytes(),
                controls=controls(**{required_capability: True}),
            )
        )

    assert exc.value.code == "unsupported_capability"
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "required_capability",
    ["require_streaming_audio_output", "require_barge_in"],
)
async def test_buffered_synthesis_fails_closed_for_realtime_requirements(
    required_capability: str,
):
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"audio")

    adapter = OpenAICompatibleSpeechSynthesisAdapter(audio_config(), client=async_client(handler))
    with pytest.raises(ProviderError) as exc:
        async for _ in adapter.synthesize(
            SpeechRequest(
                text="hello",
                controls=controls(**{required_capability: True}),
            )
        ):
            pass

    assert exc.value.code == "unsupported_capability"
    assert called is False


@pytest.mark.asyncio
async def test_speaches_rejects_verbatim_and_unsupported_formats_before_http():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    adapter = SpeachesKokoroAdapter(SpeachesConfig(), client=async_client(handler))
    with pytest.raises(ProviderError) as exc:
        async for _ in adapter.synthesize(
            SpeechRequest(text="hello", controls=controls(require_verbatim_output=True))
        ):
            pass
    assert exc.value.code == "unsupported_capability"
    with pytest.raises(ProviderError) as exc:
        async for _ in adapter.synthesize(
            SpeechRequest(text="hello", output_format="unsupported", controls=controls())
        ):
            pass
    assert exc.value.code == "unsupported_capability"
    assert called is False


@pytest.mark.asyncio
async def test_http_errors_are_normalized_and_retried_only_within_budget():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "model loading"}})

    adapter = OpenAICompatibleReasoningAdapter(reasoning_config(), client=async_client(handler))
    with pytest.raises(ProviderError) as exc:
        await adapter.generate(
            ReasoningRequest(
                task="classify_intent",
                prompt="classify",
                controls=controls(max_retries=1),
            )
        )
    assert exc.value.code == "provider_unavailable"
    assert exc.value.retryable is True
    assert str(exc.value) == "provider is unavailable (HTTP 503)"
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (400, "invalid_request", False),
        (401, "provider_authentication_failed", False),
        (403, "provider_authentication_failed", False),
        (408, "provider_timeout", True),
        (429, "rate_limited", True),
        (500, "provider_unavailable", True),
        (504, "provider_timeout", True),
    ],
)
@pytest.mark.parametrize("body_format", ["json", "text"])
async def test_upstream_error_bodies_never_escape_adapter(status, code, retryable, body_format):
    secret = "https://private-provider.test/path?api_key=secret-api-key"

    async def handler(request: httpx.Request) -> httpx.Response:
        if body_format == "json":
            return httpx.Response(status, json={"error": {"message": secret}})
        return httpx.Response(status, text=secret)

    adapter = OpenAICompatibleReasoningAdapter(reasoning_config(), client=async_client(handler))
    with pytest.raises(ProviderError) as exc:
        await adapter.generate(
            ReasoningRequest(task="classify_intent", prompt="Classify", controls=controls())
        )
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    assert f"HTTP {status}" in str(exc.value)
    assert "private-provider" not in str(exc.value)
    assert "secret-api-key" not in str(exc.value)

    probe = await adapter.probe()
    assert probe.healthy is False
    assert "private-provider" not in probe.detail
    assert "secret-api-key" not in probe.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "connection", "invalid_json"])
async def test_probe_does_not_publish_raw_transport_or_decode_errors(failure):
    secret = "https://private-provider.test/path?api_key=secret-api-key"

    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure == "connection":
            raise httpx.ConnectError(secret, request=request)
        return httpx.Response(200, text=secret)

    adapter = OpenAICompatibleReasoningAdapter(reasoning_config(), client=async_client(handler))
    probe = await adapter.probe()
    assert probe.healthy is False
    assert "private-provider" not in probe.detail
    assert "secret-api-key" not in probe.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason",
    ["https://private-provider.test?api_key=secret-api-key", {"secret": "secret-api-key"}],
)
async def test_empty_response_does_not_echo_untrusted_finish_reason(finish_reason):
    adapter = OpenAICompatibleReasoningAdapter(
        reasoning_config(),
        client=async_client(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}, "finish_reason": finish_reason}]},
            )
        ),
    )
    with pytest.raises(ProviderError) as exc:
        await adapter.generate(
            ReasoningRequest(task="classify_intent", prompt="Classify", controls=controls())
        )
    assert exc.value.code == "empty_provider_response"
    assert str(exc.value) == "reasoning provider returned no final content"


@pytest.mark.asyncio
async def test_probes_health_and_required_models():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"data": [{"id": "reasoning"}]})

    reasoning = OpenAICompatibleReasoningAdapter(
        reasoning_config(reasoning_model="reasoning", check_health_endpoint=True),
        client=async_client(handler),
    )
    probe = await reasoning.probe()
    assert probe.healthy is True
    assert probe.missing_models == frozenset()

    transcription = OpenAICompatibleTranscriptionAdapter(
        audio_config(transcription_model="reasoning", synthesis_model="speech"),
        client=async_client(handler),
    )
    probe = await transcription.probe()
    assert probe.healthy is True
    assert probe.missing_models == frozenset()

    synthesis = OpenAICompatibleSpeechSynthesisAdapter(
        audio_config(transcription_model="reasoning", synthesis_model="speech"),
        client=async_client(handler),
    )
    probe = await synthesis.probe()
    assert probe.healthy is False
    assert probe.missing_models == frozenset({"speech"})


@pytest.mark.asyncio
async def test_external_provider_requires_explicit_processing_permission():
    adapter = SpeachesKokoroAdapter(
        SpeachesConfig(external_processing=True),
        client=async_client(lambda request: httpx.Response(200, content=b"audio")),
    )
    with pytest.raises(ProviderError) as exc:
        async for _ in adapter.synthesize(SpeechRequest(text="hello", controls=controls())):
            pass
    assert exc.value.code == "policy_denied"


@pytest.mark.asyncio
async def test_priority_and_retention_controls_fail_closed():
    client = async_client(lambda request: httpx.Response(200, content=b"audio"))
    prioritized = SpeachesKokoroAdapter(SpeachesConfig(), client=client)
    with pytest.raises(ProviderError) as exc:
        async for _ in prioritized.synthesize(
            SpeechRequest(text="hello", controls=controls(priority="live"))
        ):
            pass
    assert exc.value.code == "unsupported_control"

    retaining = SpeachesKokoroAdapter(SpeachesConfig(retains_provider_data=True), client=client)
    with pytest.raises(ProviderError) as exc:
        async for _ in retaining.synthesize(SpeechRequest(text="hello", controls=controls())):
            pass
    assert exc.value.code == "policy_denied"


@pytest.mark.asyncio
async def test_speaches_resolves_provider_default_voice_and_preserves_explicit_voice():
    voices = []

    async def handler(request: httpx.Request) -> httpx.Response:
        voices.append(json.loads(request.content)["voice"])
        return httpx.Response(200, content=b"audio")

    adapter = SpeachesKokoroAdapter(SpeachesConfig(), client=async_client(handler))
    for voice in (None, "explicit-voice"):
        async for _ in adapter.synthesize(
            SpeechRequest(text="Hello", voice=voice, controls=controls())
        ):
            pass
    assert voices == ["af_heart", "explicit-voice"]


@pytest.mark.asyncio
async def test_generic_synthesis_requires_voice_without_http():
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"audio")

    adapter = OpenAICompatibleSpeechSynthesisAdapter(
        audio_config(default_voice=None), client=async_client(handler)
    )
    with pytest.raises(ProviderError, match="voice must be configured") as exc:
        async for _ in adapter.synthesize(SpeechRequest(text="Hello", controls=controls())):
            pass
    assert exc.value.code == "invalid_request"
    assert called is False
