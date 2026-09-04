"""Speaches WebSocket transport and event translation for live transcription."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from agentic_interviewer.domain.realtime import (
    RealtimeAudioCommand,
    RealtimeError,
    RealtimeTranscriptEvent,
    RealtimeTranscriptionSession,
)

_UNSUPPORTED_PREFIX_PADDING_FIELD = "session.turn_detection.prefix_padding_ms"


@dataclass(frozen=True)
class SpeachesRealtimeConfig:
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    connect_timeout_seconds: float = 10.0
    max_message_bytes: int = 1_048_576
    vad_threshold: float = 0.9
    silence_duration_ms: int = 1_500


def build_speaches_realtime_websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Speaches base URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Speaches base URL must not contain credentials, query, or fragment")
    return urlunsplit(
        (
            {"http": "ws", "https": "wss"}[parsed.scheme],
            parsed.netloc,
            f"{parsed.path.rstrip('/')}/v1/realtime",
            "",
            "",
        )
    )


def build_speaches_transcription_session_update(
    *, vad_threshold: float, silence_duration_ms: int, prefix_padding_ms: int = 0
) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": vad_threshold,
                "silence_duration_ms": silence_duration_ms,
                # Speaches requires this non-configurable field. Echo its default,
                # suppress only the known warning, and still require the ACK.
                "prefix_padding_ms": prefix_padding_ms,
                "create_response": False,
            }
        },
    }


class SpeachesRealtimeAdapter:
    def __init__(
        self, config: SpeachesRealtimeConfig, *, connector: Callable[..., Any] = connect
    ) -> None:
        self.config = config
        self._connector = connector
        self._url = build_speaches_realtime_websocket_url(config.base_url)

    @asynccontextmanager
    async def open_session(self, language: str) -> AsyncIterator[RealtimeTranscriptionSession]:
        query = urlencode(
            {"model": self.config.model, "intent": "transcription", "language": language}
        )
        headers = (
            {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else None
        )
        try:
            async with self._connector(
                f"{self._url}?{query}",
                additional_headers=headers,
                open_timeout=self.config.connect_timeout_seconds,
                close_timeout=self.config.connect_timeout_seconds,
                max_size=self.config.max_message_bytes,
            ) as upstream:
                await self._configure(upstream)
                yield _SpeachesRealtimeSession(upstream, self.config.max_message_bytes)
        except TimeoutError as exc:
            raise RealtimeError("voice_timeout") from exc
        except (InvalidHandshake, OSError, ConnectionClosed) as exc:
            raise RealtimeError("voice_unavailable") from exc

    async def _configure(self, upstream: Any) -> None:
        update_sent = False
        async with asyncio.timeout(self.config.connect_timeout_seconds):
            async for message in upstream:
                event = _validated_provider_event(message, self.config.max_message_bytes)
                if event["type"] == "session.created" and not update_sent:
                    await upstream.send(
                        json.dumps(
                            build_speaches_transcription_session_update(
                                vad_threshold=self.config.vad_threshold,
                                silence_duration_ms=self.config.silence_duration_ms,
                                prefix_padding_ms=_session_prefix_padding(event),
                            )
                        )
                    )
                    update_sent = True
                elif update_sent and _is_unsupported_prefix_padding_error(event):
                    continue
                elif event["type"] == "error":
                    raise RealtimeError("voice_configuration_failed")
                elif update_sent and event["type"] == "session.updated":
                    return
        raise RealtimeError("voice_configuration_failed")


class _SpeachesRealtimeSession:
    def __init__(self, upstream: Any, max_bytes: int) -> None:
        self._upstream = upstream
        self._max_bytes = max_bytes

    async def send(self, command: RealtimeAudioCommand) -> None:
        event: dict[str, str] = {"type": f"input_audio_buffer.{command.kind}"}
        if command.kind == "append" and command.audio is not None:
            event["audio"] = command.audio
        await self._upstream.send(json.dumps(event))

    async def events(self) -> AsyncIterator[RealtimeTranscriptEvent]:
        async for message in self._upstream:
            event = _validated_provider_event(message, self._max_bytes)
            event_type = event["type"]
            if event_type == "input_audio_buffer.speech_started":
                yield RealtimeTranscriptEvent("speech_started")
            elif event_type == "input_audio_buffer.speech_stopped":
                yield RealtimeTranscriptEvent("speech_stopped")
            elif event_type == "conversation.item.input_audio_transcription.delta":
                yield RealtimeTranscriptEvent("transcript_delta", _event_text(event, "delta"))
            elif event_type == "conversation.item.input_audio_transcription.completed":
                yield RealtimeTranscriptEvent(
                    "transcript_completed", _event_text(event, "transcript")
                )
            elif event_type in {"error", "conversation.item.input_audio_transcription.failed"}:
                raise RealtimeError("voice_transcription_failed")
            # Never expose provider session metadata or unknown events.


def _validated_provider_event(message: Any, max_bytes: int) -> dict[str, Any]:
    if not isinstance(message, str) or len(message.encode("utf-8")) > max_bytes:
        raise RealtimeError("voice_invalid_response")
    try:
        event = json.loads(message)
    except json.JSONDecodeError as exc:
        raise RealtimeError("voice_invalid_response") from exc
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise RealtimeError("voice_invalid_response")
    return event


def _event_text(event: Mapping[str, Any], field_name: str) -> str:
    text = event.get(field_name, "")
    if not isinstance(text, str):
        raise RealtimeError("voice_invalid_response")
    return text


def _session_prefix_padding(event: Mapping[str, Any]) -> int:
    session = event.get("session")
    turn_detection = session.get("turn_detection") if isinstance(session, Mapping) else None
    value = turn_detection.get("prefix_padding_ms") if isinstance(turn_detection, Mapping) else 0
    return value if type(value) is int and value >= 0 else 0


def _is_unsupported_prefix_padding_error(event: Mapping[str, Any]) -> bool:
    if event.get("type") != "error":
        return False
    error = event.get("error")
    message = error.get("message") if isinstance(error, Mapping) else None
    return message == f"Specifying `{_UNSUPPORTED_PREFIX_PADDING_FIELD}` is not supported."
