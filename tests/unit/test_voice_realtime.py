from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentic_interviewer.adapters.speaches_realtime import (
    SpeachesRealtimeAdapter,
    SpeachesRealtimeConfig,
    build_speaches_realtime_websocket_url,
    build_speaches_transcription_session_update,
)
from agentic_interviewer.api.app import create_app
from agentic_interviewer.api.voice_realtime import (
    _bridge_session,
    create_voice_realtime_router,
    validate_same_origin,
)
from agentic_interviewer.config import Settings
from agentic_interviewer.domain.realtime import RealtimeAudioCommand, RealtimeError
from agentic_interviewer.services import InterviewService


def _app(settings: Settings):
    return create_app(settings, InterviewService())


def test_candidate_ui_is_hidden_when_disabled_and_available_when_enabled():
    disabled = TestClient(_app(Settings(app_env="test", candidate_ui_enabled=False)))
    assert disabled.get("/").status_code == 404
    assert disabled.get("/static/app.js").status_code == 404
    with pytest.raises(WebSocketDisconnect) as rejected:
        with disabled.websocket_connect(
            "/ws/voice/realtime", headers={"Origin": "http://testserver"}
        ):
            pass
    assert rejected.value.code == 1008

    enabled = TestClient(_app(Settings(app_env="test", candidate_ui_enabled=True)))
    response = enabled.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "candidate interview" in response.text.lower()
    assert enabled.get("/voice-lab").status_code == 404


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        ("/static/styles.css", "text/css"),
        ("/static/app.js", "text/javascript"),
        ("/static/pcm-worklet.js", "text/javascript"),
    ],
)
def test_candidate_static_assets_are_available_only_by_exact_route(path: str, media_type: str):
    api = TestClient(_app(Settings(app_env="test", candidate_ui_enabled=True)))
    response = api.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.content
    assert api.get("/static/%2e%2e/%2e%2e/config.py").status_code == 404


def test_candidate_ui_uses_server_mediated_websocket_and_supported_session_fields():
    api = TestClient(_app(Settings(app_env="test", candidate_ui_enabled=True)))
    app_js = api.get("/static/app.js").text
    page = api.get("/").text
    worklet_js = api.get("/static/pcm-worklet.js").text

    assert "RTCPeerConnection" not in app_js
    assert "/api/voice/realtime/webrtc" not in app_js
    assert 'input_audio_format: "pcm16"' not in app_js
    assert "prefix_padding_ms" not in app_js
    assert "turn_detection: null" not in app_js
    assert 'event.type === "voice.ready"' in app_js
    assert 'event.type === "voice.speech.stopped"' in app_js
    assert "Speaches" not in app_js
    assert "input_audio_buffer" not in app_js
    assert "await startRecording();" in app_js
    assert "Done speaking" in page
    assert 'type: "flush"' in app_js
    assert 'data?.type !== "flush"' in worklet_js


def test_candidate_ui_keeps_internal_planning_status_out_of_preflight_copy():
    api = TestClient(_app(Settings(app_env="test", candidate_ui_enabled=True)))
    app_js = api.get("/static/app.js").text
    page = api.get("/").text

    assert 'id="planningWarning"' in page
    assert 'id="planningAcknowledgement"' in page
    assert "Continue only for development testing" not in page
    assert "Degraded question planning" not in page
    assert "state.intakePending" in app_js
    assert "updateBeginAvailability();" in app_js


def test_server_builds_speaches_vad_update_with_model_responses_disabled():
    event = build_speaches_transcription_session_update(
        vad_threshold=0.9, silence_duration_ms=1_500, prefix_padding_ms=0
    )

    assert event == {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.9,
                "silence_duration_ms": 1_500,
                "prefix_padding_ms": 0,
                "create_response": False,
            }
        },
    }


@pytest.mark.parametrize(
    ("origin", "host", "scheme", "expected"),
    [
        ("http://localhost:8000", "localhost:8000", "ws", True),
        ("https://voice.example.test", "voice.example.test:443", "wss", True),
        ("https://evil.example.test", "voice.example.test", "wss", False),
        ("https://voice.example.test/path", "voice.example.test", "wss", False),
        (None, "localhost:8000", "ws", False),
    ],
)
def test_same_origin_validation(origin: str | None, host: str, scheme: str, expected: bool):
    assert validate_same_origin(origin, host, scheme) is expected


def test_realtime_upstream_url_is_built_only_from_configured_speaches_base():
    assert (
        build_speaches_realtime_websocket_url("https://speech.example.test/private/")
        == "wss://speech.example.test/private/v1/realtime"
    )
    with pytest.raises(ValueError, match="must not contain credentials"):
        build_speaches_realtime_websocket_url("https://user:password@speech.example.test")


class _FakeUpstream:
    def __init__(self, messages: list[str], *, stay_open: bool = False) -> None:
        self._messages = iter(messages)
        self.stay_open = stay_open
        self.cancelled = False
        self.sent: list[str] = []
        self.close_code: int | None = None

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._messages)
        except StopIteration as exc:
            if self.stay_open:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            raise StopAsyncIteration from exc

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


class _FakeConnection(AbstractAsyncContextManager[_FakeUpstream]):
    def __init__(self, upstream: _FakeUpstream) -> None:
        self.upstream = upstream

    async def __aenter__(self) -> _FakeUpstream:
        return self.upstream

    async def __aexit__(self, *args: Any) -> None:
        await self.upstream.close()


def test_websocket_validates_origin_and_constructs_fixed_upstream_url():
    app = FastAPI()
    upstream = _FakeUpstream(
        [
            json.dumps(
                {
                    "type": "session.created",
                    "session": {"turn_detection": {"prefix_padding_ms": 0}},
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "message": (
                            "Specifying `session.turn_detection.prefix_padding_ms` is not "
                            "supported."
                        )
                    },
                }
            ),
            json.dumps({"type": "session.updated", "session": {"id": "session-1"}}),
        ]
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def connector(url: str, **kwargs: Any) -> _FakeConnection:
        calls.append((url, kwargs))
        return _FakeConnection(upstream)

    adapter = SpeachesRealtimeAdapter(
        SpeachesRealtimeConfig(
            base_url="http://speech.internal:8000", api_key="secret-key", model="configured-model"
        ),
        connector=connector,
    )
    app.include_router(
        create_voice_realtime_router(Settings(app_env="test", candidate_ui_enabled=True), adapter)
    )
    api = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.websocket_connect(
            "/ws/voice/realtime", headers={"Origin": "https://evil.example.test"}
        ):
            pass
    assert rejected.value.code == 1008
    assert calls == []

    with api.websocket_connect(
        "/ws/voice/realtime?language=en-IN",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        events = [json.loads(websocket.receive_text()) for _ in range(2)]
    assert [event["type"] for event in events] == [
        "voice.connected",
        "voice.ready",
    ]
    assert calls[0][0] == (
        "ws://speech.internal:8000/v1/realtime"
        "?model=configured-model&intent=transcription&language=en-IN"
    )
    assert calls[0][1]["additional_headers"] == {"Authorization": "Bearer secret-key"}
    assert "secret-key" not in calls[0][0]
    assert json.loads(upstream.sent[0])["session"]["turn_detection"]["create_response"] is False
    assert upstream.close_code == 1000


def test_candidate_page_does_not_expose_server_configuration_or_allow_path_traversal():
    settings = Settings(
        app_env="test",
        candidate_ui_enabled=True,
        speaches_api_key="must-not-reach-browser",
        speaches_base_url="http://private-speech.internal:8000",
    )
    api = TestClient(_app(settings))
    page = api.get("/")

    assert page.status_code == 200
    assert "must-not-reach-browser" not in page.text
    assert "private-speech.internal" not in page.text
    assert api.get("/static/../../pyproject.toml").status_code == 404


def _adapter(messages: list[Any], *, stay_open: bool = False):
    upstream = _FakeUpstream(messages, stay_open=stay_open)
    adapter = SpeachesRealtimeAdapter(
        SpeachesRealtimeConfig(
            base_url="http://private-speech.internal",
            model="private-model",
            api_key="private-secret",
            connect_timeout_seconds=0.05,
        ),
        connector=lambda *args, **kwargs: _FakeConnection(upstream),
    )
    return adapter, upstream


def _connected_adapter(messages: list[Any], *, stay_open: bool = False):
    return _adapter(
        [json.dumps({"type": "session.created"}), json.dumps({"type": "session.updated"})]
        + messages,
        stay_open=stay_open,
    )


def _voice_client(adapter=None, **settings):
    app = FastAPI()
    app.include_router(
        create_voice_realtime_router(
            Settings(app_env="test", candidate_ui_enabled=True, **settings), adapter
        )
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "query", ["model=browser-model", "language=en%26url=evil", "upstream=evil"]
)
def test_realtime_rejects_model_override_unknown_parameters_and_invalid_language(query):
    api = _voice_client()
    with pytest.raises(WebSocketDisconnect) as rejected:
        with api.websocket_connect(
            f"/ws/voice/realtime?{query}", headers={"Origin": "http://testserver"}
        ):
            pass
    assert rejected.value.code == 1008


def test_unconfigured_realtime_returns_safe_error_instead_of_implicit_provider():
    with _voice_client().websocket_connect(
        "/ws/voice/realtime", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json() == {
            "type": "voice.error",
            "error": {
                "code": "voice_unavailable",
                "message": "Live transcription is currently unavailable.",
            },
        }


@pytest.mark.asyncio
async def test_adapter_translates_only_application_transcription_events_and_audio_commands():
    adapter, upstream = _connected_adapter(
        [
            json.dumps({"type": "session.updated", "secret": "private-secret"}),
            json.dumps({"type": "input_audio_buffer.speech_started"}),
            json.dumps({"type": "input_audio_buffer.speech_stopped"}),
            json.dumps(
                {"type": "conversation.item.input_audio_transcription.delta", "delta": "Hi"}
            ),
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Hi",
                }
            ),
        ]
    )
    async with adapter.open_session("en") as session:
        await session.send(RealtimeAudioCommand("append", "AAA="))
        await session.send(RealtimeAudioCommand("commit"))
        await session.send(RealtimeAudioCommand("clear"))
        events = [event async for event in session.events()]
    assert [event.kind for event in events] == [
        "speech_started",
        "speech_stopped",
        "transcript_delta",
        "transcript_completed",
    ]
    assert events[-1].text == "Hi"
    assert [json.loads(value) for value in upstream.sent[1:]] == [
        {"type": "input_audio_buffer.append", "audio": "AAA="},
        {"type": "input_audio_buffer.commit"},
        {"type": "input_audio_buffer.clear"},
    ]
    assert "private-secret" not in repr(adapter.config)


@pytest.mark.parametrize(
    "provider_message",
    [
        json.dumps(
            {"type": "error", "error": {"message": "private-secret private-speech.internal"}}
        ),
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "error": "private-secret",
            }
        ),
        "not-json private-secret",
        b"binary-secret",
        json.dumps({"type": "conversation.item.input_audio_transcription.delta", "delta": {}}),
    ],
)
def test_provider_errors_are_sanitized_and_never_forwarded(provider_message):
    adapter, _ = _connected_adapter([provider_message])
    with _voice_client(adapter).websocket_connect(
        "/ws/voice/realtime", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "voice.connected"
        assert websocket.receive_json()["type"] == "voice.ready"
        event = websocket.receive_json()
        assert event["type"] == "voice.error"
        assert "private" not in json.dumps(event)


@pytest.mark.asyncio
async def test_adapter_requires_configuration_ack_and_bounds_setup_timeout():
    adapter, upstream = _adapter([], stay_open=True)
    with pytest.raises(RealtimeError, match="voice_timeout"):
        async with adapter.open_session("en"):
            pytest.fail("an unconfigured session must not be exposed")
    assert upstream.cancelled
    assert upstream.close_code == 1000


@pytest.mark.parametrize(
    "messages",
    [
        [json.dumps({"type": "session.created"})],
        [json.dumps({"type": "session.updated"})],
        [
            json.dumps({"type": "session.created"}),
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "message": (
                            "session.turn_detection.prefix_padding_ms not supported; auth failed"
                        )
                    },
                }
            ),
            json.dumps({"type": "session.updated"}),
        ],
    ],
)
def test_no_ready_event_before_successful_configuration_ack(messages):
    adapter, _ = _adapter(messages)
    with _voice_client(adapter).websocket_connect(
        "/ws/voice/realtime", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "voice.connected"
        event = websocket.receive_json()
        assert event["type"] == "voice.error"
        assert event["error"]["code"] == "voice_configuration_failed"


@pytest.mark.parametrize(
    "event, expected_code",
    [
        ({"type": "session.update", "session": {"secret": "x"}}, "invalid_event"),
        ({"type": []}, "invalid_event"),
        ({"type": {}}, "invalid_event"),
        ({"type": "audio.append", "audio": "%%%"}, "invalid_event"),
        ({"type": "audio.append", "audio": "AA=="}, "invalid_event"),
        ({"type": "audio.clear", "model": "other"}, "invalid_event"),
        ({"type": "audio.append", "audio": "A" * 2000}, "message_too_large"),
    ],
)
def test_browser_cannot_inject_provider_events_or_invalid_audio(event, expected_code):
    adapter, upstream = _connected_adapter([], stay_open=True)
    with _voice_client(adapter, realtime_max_message_bytes=1024).websocket_connect(
        "/ws/voice/realtime", headers={"Origin": "http://testserver"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "voice.connected"
        assert websocket.receive_json()["type"] == "voice.ready"
        websocket.send_json(event)
        assert websocket.receive_json()["error"]["code"] == expected_code
    assert len(upstream.sent) == 1  # Only server-owned session configuration.
    assert upstream.cancelled
    assert upstream.close_code == 1000


@pytest.mark.asyncio
async def test_client_disconnect_cancels_session_reader_and_closes_upstream():
    adapter, upstream = _connected_adapter([], stay_open=True)

    class DisconnectedBrowser:
        async def receive(self):
            return {"type": "websocket.disconnect", "code": 1000}

    async with adapter.open_session("en") as session:
        await _bridge_session(DisconnectedBrowser(), session, 1024)
    assert upstream.cancelled
    assert upstream.close_code == 1000


@pytest.mark.asyncio
async def test_bridge_cancellation_cleans_both_tasks_and_connection():
    adapter, upstream = _connected_adapter([], stay_open=True)
    waiting = asyncio.Event()
    browser_cancelled = False

    class WaitingBrowser:
        async def receive(self):
            nonlocal browser_cancelled
            waiting.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                browser_cancelled = True
                raise

    async def run_bridge():
        async with adapter.open_session("en") as session:
            await _bridge_session(WaitingBrowser(), session, 1024)

    task = asyncio.create_task(run_bridge())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert browser_cancelled
    assert upstream.cancelled
    assert upstream.close_code == 1000
