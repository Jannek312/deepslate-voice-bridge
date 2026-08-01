"""Bridge: wires one Voice PE device connection to Deepslate sessions.

Session lifecycle is **lazy**: a Deepslate session is opened when the wake
word fires and torn down when Deepslate's server-side inactivity cut closes
it (~30 s after the conversation ends). While the session is still
connecting, mic audio is buffered and flushed on SessionReady — the connect
(~0.5 s) overlaps the user's utterance, so reply latency is unaffected.

Event mapping (see docs/superpowers/plans/2026-08-01-deepslate-voice-bridge.md):

  device wake        -> open session (if needed), refresh HA snapshot,
                        clear suppression, reset upsampler
  device mic PCM     -> upsample 16k->24k; send, or buffer until SessionReady
  device interrupt   -> user said "stop": suppress model audio until the next
                        wake or genuine user speech (the device silenced itself)
  device flush       -> follow-up window expired mid-stream: same suppression
  DS PlaybackClearBuffer -> phase "listening" (firmware flushes its queue)
  DS VAD SPEECH_ENDING->SILENCE -> phase "thinking"
  DS first audio chunk of a turn -> phase "replying", then stream chunks
  DS ResponseEnd     -> phase "idle" (device runs its own follow-up window)
  DS ToolCallRequest -> ToolExecutor -> always send_tool_response
  DS inactivity close -> expected end of conversation: silent teardown
  DS other fatal error -> phase "idle" to unstick the device; next wake retries
"""

from __future__ import annotations

import asyncio
import logging
import time

from deepslate.core import DeepslateSessionListener

from app.audio import Upsampler16to24
from app.config import Settings
from app.deepslate import SESSION_CHANNELS, SESSION_SAMPLE_RATE, build_system_prompt, create_session
from app.device_server import DeviceConnection
from app.ha_client import HAClient
from app.tools import TOOL_DEFINITIONS, ToolExecutor

logger = logging.getLogger(__name__)

SESSION_READY_TIMEOUT_S = 15.0
# Mic audio buffered while the session connects: 10 s @ 24 kHz PCM16 is
# far more than a connect ever takes; beyond that we drop oldest first.
MAX_BUFFERED_BYTES = 10 * SESSION_SAMPLE_RATE * 2
# A session death this soon after activity happened mid-conversation and is
# worth surfacing; later than this it's just the expected idle cut.
MID_CONVERSATION_WINDOW_S = 10.0


class Bridge(DeepslateSessionListener):
    def __init__(self, conn: DeviceConnection, settings: Settings, ha: HAClient):
        self._conn = conn
        self._settings = settings
        self._ha = ha
        self._session = None
        self._session_ready = False
        self._open_task: asyncio.Task | None = None
        self._executor: ToolExecutor | None = None
        self._snapshot: dict | None = None
        self._up = Upsampler16to24()
        self._buffer: list[bytes] = []
        self._buffered_bytes = 0
        self._suppressed = False   # drop model audio until next wake/real speech
        self._replying = False     # this turn already produced audible audio
        self._closed = False
        self._last_activity = 0.0

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Device connected: prefetch the HA snapshot; no Deepslate session yet."""
        self._snapshot = await self._ha.lights_snapshot()
        self._executor = ToolExecutor(self._ha, self._snapshot)

    def _ensure_session(self) -> None:
        if self._session is not None or self._closed:
            return
        if self._open_task is None or self._open_task.done():
            self._open_task = asyncio.create_task(self._open_session())

    async def _open_session(self) -> None:
        try:
            prompt = build_system_prompt(self._snapshot, self._settings)
            session = create_session(self._settings, prompt, listener=self)
            self._session = session
            self._session_ready = False
            session.start()
            # Tools BEFORE initialize: pre-init update_tools only stores the
            # list; the SDK sends it right after InitializeSessionRequest.
            await session.update_tools(TOOL_DEFINITIONS)
            # SDK footgun: initialize() silently no-ops while the WS is still
            # connecting. Poll until the server confirms with SessionReady.
            deadline = time.monotonic() + SESSION_READY_TIMEOUT_S
            while time.monotonic() < deadline and not self._closed:
                await session.initialize(SESSION_SAMPLE_RATE, SESSION_CHANNELS)
                if session.session_initialized:
                    return  # SessionReady fired; buffer flush happens there
                await asyncio.sleep(0.1)
            raise TimeoutError(f"no SessionReady within {SESSION_READY_TIMEOUT_S}s")
        except Exception as e:
            logger.error("could not open deepslate session: %r", e)
            await self._teardown(self._session, unstick=True)

    async def _teardown(self, session, unstick: bool) -> None:
        """Drop the session (idempotent). unstick=True nudges the device LEDs."""
        if session is not None and session is self._session:
            self._session = None
            self._session_ready = False
            self._buffer.clear()
            self._buffered_bytes = 0
        if session is not None:
            try:
                await session.close()
            except Exception as e:
                logger.debug("session close error: %r", e)
        if unstick and not self._closed:
            self._replying = False
            await self._conn.send_phase("idle")

    # -- DeviceHandler (device -> bridge) --------------------------------------

    async def on_start(self) -> None:
        logger.info("device session start")

    async def on_wake(self) -> None:
        logger.info("wake: new turn")
        self._suppressed = False
        self._replying = False
        self._up.reset()
        self._ensure_session()
        # Refresh the HA snapshot in the background so the running executor
        # (and the next session's prompt) see current areas/lights/states.
        asyncio.create_task(self._refresh_snapshot())

    async def _refresh_snapshot(self) -> None:
        try:
            self._snapshot = await self._ha.lights_snapshot()
            if self._executor is not None:
                self._executor.update_snapshot(self._snapshot)
        except Exception as e:
            logger.warning("snapshot refresh failed: %r", e)

    async def on_interrupt(self) -> None:
        logger.info("device interrupt (stop): suppressing model audio until next turn")
        self._suppressed = True

    async def on_flush(self) -> None:
        logger.info("follow-up window expired: suppressing until next turn")
        self._suppressed = True
        self._up.reset()

    async def on_audio(self, pcm16k: bytes) -> None:
        pcm24k = self._up.process(pcm16k)
        if not pcm24k:
            return
        self._last_activity = time.monotonic()
        self._log_mic_level(pcm24k)
        if self._session is not None and self._session_ready:
            await self._session.send_audio(pcm24k, SESSION_SAMPLE_RATE, SESSION_CHANNELS)
            return
        # Session still connecting (or being opened by this very audio, if a
        # wake was somehow missed): buffer and flush on SessionReady.
        self._ensure_session()
        self._buffer.append(pcm24k)
        self._buffered_bytes += len(pcm24k)
        while self._buffered_bytes > MAX_BUFFERED_BYTES and self._buffer:
            dropped = self._buffer.pop(0)
            self._buffered_bytes -= len(dropped)

    def _log_mic_level(self, pcm: bytes) -> None:
        """~1/s diagnostic: RMS + peak of forwarded mic audio, as a fraction of
        full scale — directly comparable to the VAD's min_volume gate."""
        now = time.monotonic()
        if now - getattr(self, "_last_level_log", 0.0) < 1.0:
            return
        self._last_level_log = now
        import array

        samples = array.array("h")
        samples.frombytes(pcm)
        if not samples:
            return
        peak = max(abs(s) for s in samples) / 32768
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768
        logger.info("mic level: rms=%.4f peak=%.4f (%d samples)", rms, peak, len(samples))

    async def on_disconnect(self) -> None:
        self._closed = True
        if self._open_task is not None:
            self._open_task.cancel()
        await self._teardown(self._session, unstick=False)

    # -- DeepslateSessionListener (Deepslate -> bridge) ------------------------

    async def on_session_initialized(self) -> None:
        self._session_ready = True
        buffered, self._buffer = self._buffer, []
        self._buffered_bytes = 0
        logger.info(
            "deepslate session ready (flushing %d buffered chunks)", len(buffered)
        )
        session = self._session
        if session is None:
            return
        for chunk in buffered:
            await session.send_audio(chunk, SESSION_SAMPLE_RATE, SESSION_CHANNELS)

    async def on_vad_state_event(
        self, from_state: str, to_state: str, session_time_ms: int, packet_id: int
    ) -> None:
        logger.info("vad %s -> %s", from_state, to_state)
        if from_state == "SPEECH_ENDING" and to_state == "SILENCE":
            # End of user turn: model is now working on a reply.
            if not self._suppressed:
                await self._conn.send_phase("thinking")
        elif to_state == "SPEECH" and self._suppressed:
            # Genuine new user speech lifts a stop/flush suppression.
            logger.info("real speech detected: lifting suppression")
            self._suppressed = False

    async def on_playback_buffer_clear(self) -> None:
        # User barged in: the firmware flushes its playback queue on the
        # transition to "listening".
        self._replying = False
        await self._conn.send_phase("listening")

    async def on_response_begin(self, turn_id: int = 0) -> None:
        logger.info("response begin (turn %d)", turn_id)
        self._replying = False

    async def on_audio_chunk(
        self, pcm_bytes: bytes, sample_rate: int, channels: int, transcript=None
    ) -> None:
        if self._suppressed:
            return
        self._last_activity = time.monotonic()
        if sample_rate != SESSION_SAMPLE_RATE or channels != SESSION_CHANNELS:
            logger.warning(
                "unexpected model audio format %dHz/%dch (expected %d/%d) — forwarding anyway",
                sample_rate, channels, SESSION_SAMPLE_RATE, SESSION_CHANNELS,
            )
        if not self._replying:
            self._replying = True
            await self._conn.send_phase("replying")
        await self._conn.send_audio(pcm_bytes)

    async def on_response_end(self, turn_id: int = 0) -> None:
        if self._replying and not self._suppressed:
            await self._conn.send_phase("idle")
        self._replying = False

    async def on_tool_call(self, call_id: str, name: str, params: dict, turn_id=None) -> None:
        logger.info("tool call %s(%s)", name, params)
        result = await self._executor.execute(name, params)
        logger.info("tool result: %.200s", result)
        await self._session.send_tool_response(call_id, result)

    async def on_user_transcription(self, text: str, language=None, turn_id: int = 0) -> None:
        logger.info("user said (%s): %s", language, text)

    async def on_error(self, category: str, message: str, trace_id=None) -> None:
        if category == "ERROR_SESSION" and "inactivity" in message.lower():
            # Expected lifecycle: the conversation ended and Deepslate reaped
            # the idle session. Tear down quietly (stops the SDK's reconnect
            # loop); the next wake opens a fresh session.
            logger.info("deepslate closed idle session (conversation over)")
            asyncio.create_task(self._teardown(self._session, unstick=False))
            return
        logger.error("deepslate error [%s] %s (trace: %s)", category, message, trace_id)

    async def on_fatal_error(self, e: Exception) -> None:
        if self._session is None:
            return  # already torn down (e.g. expected inactivity close)
        mid_conversation = time.monotonic() - self._last_activity < MID_CONVERSATION_WINDOW_S
        if mid_conversation:
            logger.error("deepslate session died mid-conversation: %r", e)
        else:
            logger.info("deepslate session ended (%r)", e)
        asyncio.create_task(self._teardown(self._session, unstick=mid_conversation))
