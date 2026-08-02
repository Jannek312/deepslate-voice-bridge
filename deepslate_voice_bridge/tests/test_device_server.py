import asyncio
import json

import pytest
import websockets

from app.config import Settings
from app.device_server import DeviceServer


class RecordingHandler:
    def __init__(self, conn):
        self.conn = conn
        self.events = []
        self.audio = b""
        self.disconnected = asyncio.Event()

    async def on_start(self):
        self.events.append("start")

    async def on_wake(self):
        self.events.append("wake")

    async def on_interrupt(self):
        self.events.append("interrupt")

    async def on_flush(self):
        self.events.append("flush")

    async def on_audio(self, pcm):
        self.audio += pcm

    async def on_disconnect(self):
        self.disconnected.set()


@pytest.fixture
async def server():
    settings = Settings(port=0)  # pick a free port
    handlers = []

    async def factory(conn):
        h = RecordingHandler(conn)
        handlers.append(h)
        return h

    srv = DeviceServer(settings, factory, host="127.0.0.1")
    # patch: websockets.serve with port 0 needs the actual bound port
    await srv.start()
    port = next(iter(srv._server.sockets)).getsockname()[1]
    srv.url = f"ws://127.0.0.1:{port}/"
    srv.handlers = handlers
    yield srv
    await srv.stop()


async def test_hello_first_and_compact(server):
    async with websockets.connect(server.url) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=2)
        assert '"type":"hello"' in raw  # compact separators
        hello = json.loads(raw)
        assert hello["audio_out"] == "pcm"
        for key in (
            "follow_up_ms",
            "follow_up_open_delay_ms",
            "wake_open_delay_ms",
            "playback_prebuffer_ms",
        ):
            assert isinstance(hello[key], int)


async def test_dispatch_and_audio(server):
    async with websockets.connect(server.url) as ws:
        await ws.recv()  # hello
        for t in ("start", "wake", "interrupt", "flush"):
            await ws.send(json.dumps({"type": t}))
        await ws.send(b"\x01\x02\x03\x04")
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        assert pong == {"type": "pong"}
    handler = server.handlers[0]
    await asyncio.wait_for(handler.disconnected.wait(), timeout=2)
    assert handler.events == ["start", "wake", "interrupt", "flush"]
    assert handler.audio == b"\x01\x02\x03\x04"


async def test_phase_is_compact(server):
    async with websockets.connect(server.url) as ws:
        await ws.recv()  # hello
        handler = server.handlers[-1]
        await handler.conn.send_phase("listening")
        raw = await asyncio.wait_for(ws.recv(), timeout=2)
        assert '"value":"listening"' in raw


async def test_send_audio_binary(server):
    async with websockets.connect(server.url) as ws:
        await ws.recv()  # hello
        handler = server.handlers[-1]
        await handler.conn.send_audio(b"\x0a\x0b")
        raw = await asyncio.wait_for(ws.recv(), timeout=2)
        assert raw == b"\x0a\x0b"
