from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentic_interviewer.api.app import create_app
from agentic_interviewer.composition.providers import build_provider_runtime
from agentic_interviewer.config import Settings
from agentic_interviewer.domain.realtime import RealtimeTranscriptEvent
from agentic_interviewer.security import Principal, TokenService
from agentic_interviewer.services import InterviewService

SECRET = "realtime-integration-test-secret-0001"


class RecordingRealtime:
    def __init__(self):
        self.languages = []

    @asynccontextmanager
    async def open_session(self, language):
        self.languages.append(language)
        yield self

    async def send(self, command):
        pass

    async def events(self):
        yield RealtimeTranscriptEvent("transcript_completed", "A candidate answer.")


def realtime_client():
    settings = Settings(
        _env_file=None,
        app_env="test",
        provider_profile="fake",
        database_url=None,
        sqlite_path=":memory:",
        auth_required=True,
        auth_token_secret=SECRET,
    )
    realtime = RecordingRealtime()
    runtime = build_provider_runtime(settings)
    runtime.realtime = realtime
    return TestClient(create_app(settings, InterviewService(), providers=runtime)), realtime


@pytest.mark.parametrize("role", [None, "recruiter", "reviewer"])
def test_realtime_rejects_missing_token_or_wrong_role_before_provider_connection(role):
    client, realtime = realtime_client()
    headers = {"Origin": "http://testserver"}
    if role:
        token = TokenService(SECRET).issue(Principal("user", "tenant", frozenset({role})))
        headers["Authorization"] = f"Bearer {token}"
    with client, pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/voice/realtime", headers=headers):
            pytest.fail("unauthorized realtime connection was accepted")
    assert exc_info.value.code == 1008
    assert realtime.languages == []


def test_realtime_accepts_authenticated_candidate_through_injected_port():
    client, realtime = realtime_client()
    token = TokenService(SECRET).issue(Principal("candidate", "tenant", frozenset({"candidate"})))
    with client, client.websocket_connect(
        "/ws/voice/realtime?language=en",
        headers={"Origin": "http://testserver", "Authorization": f"Bearer {token}"},
    ) as socket:
        assert socket.receive_json()["type"] == "voice.connected"
        assert socket.receive_json()["type"] == "voice.ready"
        transcript = socket.receive_json()
        assert transcript["type"] == "voice.transcript.completed"
        assert transcript["text"] == "A candidate answer."
    assert realtime.languages == ["en"]
