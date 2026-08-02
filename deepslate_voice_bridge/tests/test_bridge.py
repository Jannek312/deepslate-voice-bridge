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
        self.snapshots = 0

    async def lights_snapshot(self):
        self.snapshots += 1
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

    @property
    def session_initialized(self):
        return self.initialized is not None

    async def initialize(self, sample_rate, channels):
        self.initialized = (sample_rate, channels)

    async def update_tools(self, tools):
        self.tools = tools

    async def send_audio(self, pcm, sample_rate, channels, trigger=None):
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
    ha = FakeHA()
    b = Bridge(conn, Settings(vendor_id="v", org_id="o", api_key="k"), ha)
    await b.start()
    b.conn = conn
    b.ha = ha
    return b


async def wake(b):
    """Wake and complete the session-open handshake like the real SDK would."""
    await b.on_wake()
    await b._open_task
    await b.on_session_initialized()  # SDK fires this on SessionReady
    await asyncio.sleep(0)  # let the snapshot-refresh task run
    return FakeSession.instances[-1]


async def test_start_does_not_open_session(bridge):
    assert bridge._session is None
    assert FakeSession.instances == []


async def test_wake_opens_session_with_tools(bridge):
    session = await wake(bridge)
    assert session.initialized == (24000, 1)
    assert {t["function"]["name"] for t in session.tools} == {"control_lights", "get_lights"}
    assert bridge.ha.snapshots >= 2  # start + wake refresh


async def test_audio_buffered_until_ready(bridge):
    await bridge.on_wake()
    await bridge._open_task
    session = FakeSession.instances[-1]
    await bridge.on_audio(b"\x01\x00" * 160)
    assert session.audio == []  # not ready yet -> buffered
    await bridge.on_session_initialized()
    assert len(session.audio) == 1  # buffer flushed
    await bridge.on_audio(b"\x01\x00" * 160)
    assert len(session.audio) == 2  # now direct


async def test_full_turn_phase_flow(bridge):
    session = await wake(bridge)
    await bridge.on_audio(b"\x00\x00" * 160)
    (pcm, rate, ch) = session.audio[-1]
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
    await wake(bridge)
    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x11\x22", 24000, 1, None)
    await bridge.on_playback_buffer_clear()
    assert bridge.conn.phases[-1] == "listening"
    await bridge.on_response_begin(2)
    await bridge.on_audio_chunk(b"\x55\x66", 24000, 1, None)
    assert bridge.conn.phases[-1] == "replying"


async def test_interrupt_suppresses_audio_until_wake(bridge):
    await wake(bridge)
    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x11\x22", 24000, 1, None)
    await bridge.on_interrupt()
    await bridge.on_audio_chunk(b"\x33\x44", 24000, 1, None)
    assert bridge.conn.audio == b"\x11\x22"  # second chunk dropped
    await bridge.on_wake()  # fresh wake lifts suppression (session already open)
    await bridge.on_response_begin(2)
    await bridge.on_audio_chunk(b"\x55\x66", 24000, 1, None)
    assert bridge.conn.audio == b"\x11\x22\x55\x66"


async def test_real_speech_lifts_suppression(bridge):
    await wake(bridge)
    await bridge.on_interrupt()
    await bridge.on_vad_state_event("SILENCE", "SPEECH", 0, 1)
    await bridge.on_response_begin(1)
    await bridge.on_audio_chunk(b"\x77\x88", 24000, 1, None)
    assert bridge.conn.audio.endswith(b"\x77\x88")


async def test_tool_call_roundtrip(bridge):
    session = await wake(bridge)
    await bridge.on_tool_call("call-1", "control_lights", {"area": "bedroom", "action": "on"})
    assert session.tool_responses == [("call-1", "OK: turned on all lights in Bedroom.")]
    assert bridge.ha.calls == [("light", "turn_on", {"area_id": "bedroom"})]
    # LED hints bracket the execution: dark blue during, cleared after
    assert bridge.conn.phases[-2:] == ["tool_call", "hint_clear"]


async def test_user_speech_led_hint(bridge):
    await wake(bridge)
    await bridge.on_vad_state_event("SILENCE", "SPEECH_STARTING", 0, 1)
    assert "user_speech" not in bridge.conn.phases  # STARTING alone is not speech
    await bridge.on_vad_state_event("SPEECH_STARTING", "SPEECH", 0, 1)
    assert bridge.conn.phases[-1] == "user_speech"
    await bridge.on_vad_state_event("SPEECH", "SPEECH_ENDING", 0, 1)
    await bridge.on_vad_state_event("SPEECH_ENDING", "SILENCE", 0, 1)
    assert bridge.conn.phases[-1] == "thinking"  # real phase clears the hint device-side


async def test_tool_failure_still_answers(bridge):
    session = await wake(bridge)
    await bridge.on_tool_call("call-2", "control_lights", {"area": "garage", "action": "on"})
    call_id, result = session.tool_responses[-1]
    assert call_id == "call-2" and result.startswith("Error")


async def test_inactivity_close_is_quiet_teardown(bridge):
    session = await wake(bridge)
    bridge.conn.phases.clear()
    await bridge.on_error("ERROR_SESSION", "Session closed due to inactivity.")
    await asyncio.sleep(0.01)
    assert session.closed
    assert bridge._session is None
    assert bridge.conn.phases == []  # no unstick needed — device is idle anyway


async def test_wake_after_inactivity_opens_fresh_session(bridge):
    await wake(bridge)
    await bridge.on_error("ERROR_SESSION", "Session closed due to inactivity.")
    await asyncio.sleep(0.01)
    second = await wake(bridge)
    assert len(FakeSession.instances) == 2
    assert second.session_initialized


async def test_mid_conversation_death_unsticks_device(bridge):
    import time

    session = await wake(bridge)
    await bridge.on_audio(b"\x01\x00" * 160)  # marks recent activity
    bridge._last_activity = time.monotonic()
    await bridge.on_fatal_error(RuntimeError("boom"))
    await asyncio.sleep(0.01)
    assert session.closed
    assert bridge.conn.phases[-1] == "idle"


async def test_disconnect_closes_session(bridge):
    session = await wake(bridge)
    await bridge.on_disconnect()
    assert session.closed
    assert bridge._session is None


async def test_background_audio_lifecycle(monkeypatch):
    FakeSession.instances = []
    import app.bridge as bm
    monkeypatch.setattr(bm, "create_session", lambda s, p, listener: FakeSession())
    conn = FakeConn()
    conn.bg = []
    conn.send_bg_start = lambda url: _record(conn, ("start", url))
    conn.send_bg_stop = lambda: _record(conn, ("stop",))
    settings = Settings(vendor_id="v", org_id="o", api_key="k",
                        background_audio_url="https://x/amb.mp3")
    b = Bridge(conn, settings, FakeHA())
    await b.start()
    await b.on_wake()
    assert conn.bg == [("start", "https://x/amb.mp3")]
    await b.on_wake()  # second wake inside session: no restart
    assert len(conn.bg) == 1
    await b.on_flush()  # session over
    assert conn.bg[-1] == ("stop",)
    await b.on_wake()   # new session restarts it
    assert conn.bg[-1] == ("start", "https://x/amb.mp3")
    await b.on_interrupt()
    assert conn.bg[-1] == ("stop",)


def _record(conn, item):
    async def _coro():
        conn.bg.append(item)
    return _coro()


async def test_no_background_audio_when_unconfigured(bridge):
    await wake(bridge)  # bridge fixture has no bg url; FakeConn lacks send_bg_start
    # reaching here without AttributeError proves no bg calls were attempted
