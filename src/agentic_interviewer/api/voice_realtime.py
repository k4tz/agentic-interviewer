from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from agentic_interviewer.config import Settings
from agentic_interviewer.domain.realtime import (
    RealtimeAudioCommand,
    RealtimeError,
    RealtimeTranscriptionPort,
    RealtimeTranscriptionSession,
)

_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_WEB_DIRECTORY = Path(__file__).resolve().parent.parent / "web"
_PUBLIC_ERRORS = {
    "voice_timeout": ("The speech service did not respond in time.", 1013),
    "voice_unavailable": ("Live transcription is currently unavailable.", 1013),
    "voice_configuration_failed": ("The speech session could not be prepared.", 1011),
    "voice_transcription_failed": (
        "This response could not be transcribed. Please try again.",
        1011,
    ),
    "voice_invalid_response": ("The speech session returned an invalid response.", 1011),
}


def validate_same_origin(origin: str | None, host: str | None, request_scheme: str) -> bool:
    """Return whether a browser Origin exactly matches the request's public origin."""
    if not origin or not host:
        return False
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            return False
        if parsed.query or parsed.fragment:
            return False
        expected_scheme = "https" if request_scheme in {"https", "wss"} else "http"
        return parsed.scheme == expected_scheme and _canonical_authority(parsed) == _canonical_host(
            host, expected_scheme
        )
    except ValueError:
        return False


def create_voice_realtime_router(
    settings: Settings,
    realtime: RealtimeTranscriptionPort | None = None,
    *,
    authorize_connection: Callable[[WebSocket], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", include_in_schema=False)
    async def candidate_interview() -> FileResponse:
        _require_candidate_ui(settings)
        return _web_file("index.html", "text/html; charset=utf-8", no_cache=True)

    @router.get("/static/styles.css", include_in_schema=False)
    async def candidate_styles() -> FileResponse:
        _require_candidate_ui(settings)
        return _web_file("styles.css", "text/css; charset=utf-8")

    @router.get("/static/app.js", include_in_schema=False)
    async def candidate_script() -> FileResponse:
        _require_candidate_ui(settings)
        return _web_file("app.js", "text/javascript; charset=utf-8", no_cache=True)

    @router.get("/static/pcm-worklet.js", include_in_schema=False)
    async def candidate_worklet() -> FileResponse:
        _require_candidate_ui(settings)
        return _web_file("pcm-worklet.js", "text/javascript; charset=utf-8", no_cache=True)

    @router.websocket("/ws/voice/realtime")
    async def realtime_websocket(websocket: WebSocket) -> None:
        if not settings.candidate_ui_enabled:
            await websocket.close(code=1008, reason="candidate interface is disabled")
            return
        if not validate_same_origin(
            websocket.headers.get("origin"), websocket.headers.get("host"), websocket.url.scheme
        ):
            await websocket.close(code=1008, reason="same-origin connection required")
            return
        try:
            language = _language_from_request(websocket.query_params)
            if authorize_connection is not None:
                authorize_connection(websocket)
            elif settings.auth_required:
                raise ValueError("authenticated realtime is not configured")
        except (ValueError, HTTPException):
            await websocket.close(code=1008, reason="invalid or unauthorized realtime request")
            return

        await websocket.accept()
        try:
            if realtime is None:
                raise RealtimeError("voice_unavailable")
            await websocket.send_json({"type": "voice.connected"})
            async with realtime.open_session(language) as session:
                await websocket.send_json({"type": "voice.ready"})
                await _bridge_session(websocket, session, settings.realtime_max_message_bytes)
            await _close_browser(websocket, 1000)
        except RealtimeError as exc:
            code = exc.code if exc.code in _PUBLIC_ERRORS else "voice_unavailable"
            message, close_code = _PUBLIC_ERRORS[code]
            await _send_error(websocket, code, message, close_code)
        except WebSocketDisconnect:
            return
        except Exception:
            await _send_error(
                websocket, "voice_error", "The speech connection closed unexpectedly.", 1011
            )

    return router


async def _bridge_session(
    websocket: WebSocket, session: RealtimeTranscriptionSession, max_bytes: int
) -> None:
    async def browser_to_session() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None:
                await _send_error(
                    websocket, "binary_not_supported", "Only text events are accepted", 1003
                )
                return
            if len(text.encode("utf-8")) > max_bytes:
                await _send_error(
                    websocket, "message_too_large", "Realtime event is too large", 1009
                )
                return
            try:
                command = _audio_command(text)
            except ValueError:
                await _send_error(websocket, "invalid_event", "Invalid realtime audio event", 1008)
                return
            await session.send(command)

    async def session_to_browser() -> None:
        event_types = {
            "speech_started": "voice.speech.started",
            "speech_stopped": "voice.speech.stopped",
            "transcript_delta": "voice.transcript.delta",
            "transcript_completed": "voice.transcript.completed",
        }
        async for event in session.events():
            payload = {"type": event_types[event.kind]}
            if event.kind in {"transcript_delta", "transcript_completed"}:
                payload["text"] = event.text
            if len(json.dumps(payload).encode("utf-8")) > max_bytes:
                raise RealtimeError("voice_invalid_response")
            await websocket.send_json(payload)

    tasks = {
        asyncio.create_task(browser_to_session()),
        asyncio.create_task(session_to_browser()),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _language_from_request(query_params: Mapping[str, str]) -> str:
    if set(query_params) - {"language"}:
        raise ValueError("unsupported realtime query parameter")
    language = query_params.get("language", "en").strip()
    if len(language) > 64 or not _LANGUAGE_PATTERN.fullmatch(language):
        raise ValueError("invalid language tag")
    return language


def _audio_command(text: str) -> RealtimeAudioCommand:
    try:
        event = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(event, dict):
        raise ValueError("invalid event")
    event_type = event.get("type")
    if not isinstance(event_type, str):
        raise ValueError("invalid event type")
    if event_type in {"audio.clear", "audio.commit"} and set(event) == {"type"}:
        return RealtimeAudioCommand("clear" if event_type == "audio.clear" else "commit")
    if event_type == "audio.append" and set(event) == {"type", "audio"}:
        audio = event["audio"]
        if not isinstance(audio, str) or not audio:
            raise ValueError("invalid audio")
        try:
            pcm = base64.b64decode(audio, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 audio") from exc
        if not pcm or len(pcm) % 2:
            raise ValueError("invalid PCM16 audio")
        return RealtimeAudioCommand("append", audio)
    raise ValueError("unsupported event")


def _require_candidate_ui(settings: Settings) -> None:
    if not settings.candidate_ui_enabled:
        raise HTTPException(status_code=404, detail="candidate interface is disabled")


def _web_file(filename: str, media_type: str, *, no_cache: bool = False) -> FileResponse:
    headers = {"Cache-Control": "no-store"} if no_cache else None
    return FileResponse(_WEB_DIRECTORY / filename, media_type=media_type, headers=headers)


async def _send_error(websocket: WebSocket, code: str, message: str, close_code: int) -> None:
    if websocket.client_state == WebSocketState.CONNECTED:
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "voice.error", "error": {"code": code, "message": message}}
            )
    await _close_browser(websocket, close_code)


async def _close_browser(websocket: WebSocket, code: int) -> None:
    if websocket.client_state == WebSocketState.CONNECTED:
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=code)


def _canonical_authority(parsed: Any) -> tuple[str, int]:
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname.lower(), parsed.port or default_port


def _canonical_host(host: str, scheme: str) -> tuple[str, int]:
    parsed = urlsplit(f"{scheme}://{host}")
    if not parsed.hostname:
        raise ValueError("invalid host")
    default_port = 443 if scheme == "https" else 80
    return parsed.hostname.lower(), parsed.port or default_port
