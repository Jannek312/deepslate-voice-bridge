"""WebSocket server speaking the Voice PE va_client thin-client protocol.

Wire format:
  device→server TEXT  : {"type": "start"|"wake"|"interrupt"|"flush"|"ping"}
  device→server BINARY: raw PCM16 mono 16 kHz mic audio
  server→device TEXT  : hello / phase / pong — MUST be compact JSON: the
                        firmware substring-matches '"value":"<phase>"'.
  server→device BINARY: raw PCM16 mono 24 kHz reply audio
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Protocol

import websockets
from websockets.asyncio.server import ServerConnection

from app.config import Settings

logger = logging.getLogger(__name__)


def _compact(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))


class DeviceHandler(Protocol):
    async def on_start(self) -> None: ...
    async def on_wake(self) -> None: ...
    async def on_interrupt(self) -> None: ...
    async def on_flush(self) -> None: ...
    async def on_audio(self, pcm16k: bytes) -> None: ...
    async def on_disconnect(self) -> None: ...


class DeviceConnection:
    """One connected Voice PE device."""

    def __init__(self, websocket: ServerConnection, settings: Settings):
        self._ws = websocket
        self._settings = settings
        self.remote = str(websocket.remote_address[0]) if websocket.remote_address else "?"

    async def send_hello(self) -> None:
        await self._send_json(
            {
                "type": "hello",
                "audio_out": "pcm",
                "follow_up_ms": self._settings.follow_up_ms,
                "follow_up_open_delay_ms": self._settings.follow_up_open_delay_ms,
                "wake_open_delay_ms": self._settings.wake_open_delay_ms,
                "playback_prebuffer_ms": self._settings.playback_prebuffer_ms,
            }
        )

    async def send_phase(self, value: str) -> None:
        logger.info("phase -> %s", value)
        await self._send_json({"type": "phase", "value": value})

    async def send_pong(self) -> None:
        await self._send_json({"type": "pong"})

    async def send_audio(self, pcm24k: bytes) -> None:
        try:
            await self._ws.send(pcm24k)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _send_json(self, obj: dict) -> None:
        try:
            await self._ws.send(_compact(obj))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("device %s: could not send %s (closed)", self.remote, obj.get("type"))


class DeviceServer:
    def __init__(
        self,
        settings: Settings,
        handler_factory: Callable[[DeviceConnection], Awaitable[DeviceHandler]],
        host: str = "0.0.0.0",
    ):
        self._settings = settings
        self._handler_factory = handler_factory
        self._host = host
        self._server = None

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle, self._host, self._settings.port, max_size=2**22
        )
        logger.info("device server listening on ws://%s:%d/", self._host, self._settings.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, websocket: ServerConnection) -> None:
        conn = DeviceConnection(websocket, self._settings)
        logger.info("device connected from %s", conn.remote)
        await conn.send_hello()
        handler = await self._handler_factory(conn)
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await handler.on_audio(message)
                    continue
                try:
                    data = json.loads(message)
                except (ValueError, TypeError):
                    logger.debug("non-JSON text frame ignored: %.80s", message)
                    continue
                msg_type = data.get("type")
                if msg_type == "ping":
                    await conn.send_pong()
                elif msg_type == "start":
                    await handler.on_start()
                elif msg_type == "wake":
                    await handler.on_wake()
                elif msg_type == "interrupt":
                    await handler.on_interrupt()
                elif msg_type == "flush":
                    await handler.on_flush()
                else:
                    logger.debug("unknown device message type: %s", msg_type)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            logger.info("device %s disconnected", conn.remote)
            await handler.on_disconnect()
