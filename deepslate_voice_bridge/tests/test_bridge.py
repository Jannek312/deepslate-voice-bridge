import asyncio

import pytest

import app.bridge as bridge_mod
from app.bridge import Bridge
from app.config import Settings

SNAPSHOT = {
    "areas": [
        {
            "id": "bedroom",
            "name": "Bedroom",
            "lights": [{"entity_id": "light.bedroom_bedroom", "name": "Bedroom", "state": "off"}],
        }
    ]
}


class FakeConn:
    def __init__(self):
        self.phases = []
        self.audio = b""

    async def send_phase(self, value):
        self.phases.append(value)

    async def send_audio(self, pcm):
        self.audio += pcm


class FakeHA:
    def __init__(self):
        self.calls = []

    async def lights_snapshot(self):
        return SNAPSHOT

    async def call_service(self, domain, service, data):
        self.calls.append((domain, service, data))
        return []


class FakeSession:
    instances = []

    def __init__(self):
        self.audio = []
        self.tools = None
        self.tool_responses = []
        self.closed = False
        self.initialized = None
        FakeSession.instances.append(self)

    def start(self):
        pass

    async def initialize(self, sample_rate, channels):
        self.initialized = (sample_rate, channels)

    async def update_tools(self, tools):
        self.tools = tools

    async def send_audio(self, pcm, sample_rate, channels):
        self.audio.append((pcm, sample_rate, channels))

    async def send_tool_response(self, call_id, result):
        self.tool_responses.append((call_id, result))

    async def close(self):
        self.closed = True


@pytest.fixture
async def bridge(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(bridge_mod, "create_session", lambda s, p, listener: FakeSession())
    conn = FakeConn()
    b = Bridge(conn, Settings(vendor_id="v", org_id="o", api_key="k"), FakeHA())
    await b.start()
    b.conn = conn
    b.session = FakeSession.instances[-1]
    return b


async def test_start_initializes_session_and_tools(bridge):
    assert bridge.session.initialized == (24000, 1)
    names = {t["function"]["name"] for t in bridge.session.tools}
    assert names == {"control_lights", "get_lights"}


async def test_full_turn_phase_flow(bridge):
    await bridge.on_wake()
    await bridge.on_audio(b"\x00\x00" * 160)  # 160 samples @16k -> ~240 @24k
    assert len(bridge.session.audio) == 1
    pcm, rate, ch = bridge.session.audio[0]
    assert (rate, ch) == (24000, 1)

    await bridge.on_vad_state_event("SPEECH", "SPEECH_ENDING", 0, 1)
    assert bridge.conn.phases == []  # bouncing into ENDING is not end-of-turn
    await bridge.on_vad_state_event("SPEECH_ENDING", "SILENCE", 0, 1)
    assert bridge.conn.phases == ["thinking"]

    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x11\x22", 24000, 1, None)
    await bridge.on_audio_chunk(b"\x33\x44", 24000, 1, None)
    assert bridge.conn.phases == ["thinking", "replying"]
    assert bridge.conn.audio == b"\x11\x22\x33\x44"

    await bridge.on_response_end(1)
    assert bridge.conn.phases[-1] == "idle"


async def test_barge_in_sends_listening(bridge):
    await bridge.on_wake()
    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x11\x22", 24000, 1, None)
    await bridge.on_playback_buffer_clear()
    assert bridge.conn.phases[-1] == "listening"
    # next chunk of the *new* response re-enters replying
    await bridge.on_response_begin(2)
    await bridge.on_audio_chunk(b"\x55\x66", 24000, 1, None)
    assert bridge.conn.phases[-1] == "replying"


async def test_interrupt_suppresses_audio_until_wake(bridge):
    await bridge.on_wake()
    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x11\x22", 24000, 1, None)
    await bridge.on_interrupt()
    await bridge.on_audio_chunk(b"\x33\x44", 24000, 1, None)
    assert bridge.conn.audio == b"\x11\x22"  # second chunk dropped
    # a fresh wake lifts suppression
    await bridge.on_wake()
    await bridge.on_response_begin(2)
    await bridge.on_audio_chunk(b"\x55\x66", 24000, 1, None)
    assert bridge.conn.audio == b"\x11\x22\x55\x66"


async def test_real_speech_lifts_suppression(bridge):
    await bridge.on_interrupt()
    await bridge.on_vad_state_event("SILENCE", "SPEECH", 0, 1)
    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x77\x88", 24000, 1, None)
    assert bridge.conn.audio.endswith(b"\x77\x88")


async def test_tool_call_roundtrip(bridge):
    await bridge.on_tool_call("call-1", "control_lights", {"area": "bedroom", "action": "on"})
    assert bridge.session.tool_responses == [("call-1", "OK: turned on all lights in Bedroom.")]


async def test_tool_failure_still_answers(bridge):
    await bridge.on_tool_call("call-2", "control_lights", {"area": "garage", "action": "on"})
    call_id, result = bridge.session.tool_responses[-1]
    assert call_id == "call-2"
    assert result.startswith("Error")


async def test_fatal_error_recreates_session(bridge):
    first = bridge.session
    await bridge.on_fatal_error(RuntimeError("boom"))
    assert bridge.conn.phases[-1] == "idle"
    await asyncio.wait_for(bridge._reconnect_task, timeout=2)
    assert first.closed
    assert len(FakeSession.instances) == 2  # a fresh session was created


async def test_disconnect_closes_session(bridge):
    await bridge.on_disconnect()
    assert bridge.session.closed
