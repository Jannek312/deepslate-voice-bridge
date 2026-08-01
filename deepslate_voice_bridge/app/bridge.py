"""Bridge: wires one Voice PE device connection to one Deepslate session.

Event mapping (see docs/superpowers/plans/2026-08-01-deepslate-voice-bridge.md):

  device wake        -> new turn boundary; clear suppression; reset upsampler
  device mic PCM     -> upsample 16k->24k, forward to Deepslate
  device interrupt   -> user said "stop": suppress model audio until the next
                        wake or genuine user speech (the device silenced itself)
  device flush       -> follow-up window expired mid-stream: same suppression,
                        so a stale half-utterance can't play a ghost reply
  DS PlaybackClearBuffer -> phase "listening" (firmware flushes its queue)
  DS VAD SPEECH_ENDING->SILENCE -> phase "thinking"
  DS first audio chunk of a turn -> phase "replying", then stream chunks
  DS ResponseEnd     -> phase "idle" (device runs its own follow-up window)
  DS ToolCallRequest -> ToolExecutor -> always send_tool_response
  DS fatal error     -> phase "idle", recreate the session with backoff
"""

from __future__ import annotations

import asyncio
import logging

from deepslate.core import DeepslateSessionListener

from app.audio import Upsampler16to24
from app.config import Settings
from app.deepslate import SESSION_CHANNELS, SESSION_SAMPLE_RATE, build_system_prompt, create_session
from app.device_server import DeviceConnection
from app.ha_client import HAClient
from app.tools import TOOL_DEFINITIONS, ToolExecutor

logger = logging.getLogger(__name__)

RECONNECT_INITIAL_S = 1.0
RECONNECT_MAX_S = 30.0


class Bridge(DeepslateSessionListener):
    def __init__(self, conn: DeviceConnection, settings: Settings, ha: HAClient):
        self._conn = conn
        self._settings = settings
        self._ha = ha
        self._session = None
        self._executor: ToolExecutor | None = None
        self._up = Upsampler16to24()
        self._suppressed = False   # drop model audio until next wake/real speech
        self._replying = False     # this turn already produced audible audio
        self._closed = False
        self._reconnect_task: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        snapshot = await self._ha.lights_snapshot()
        self._executor = ToolExecutor(self._ha, snapshot)
        prompt = build_system_prompt(snapshot, self._settings)
        self._session = create_session(self._settings, prompt, listener=self)
        self._session.start()
        await self._session.initialize(SESSION_SAMPLE_RATE, SESSION_CHANNELS)
        await self._session.update_tools(TOOL_DEFINITIONS)
        logger.info("deepslate session started (%d tools)", len(TOOL_DEFINITIONS))

    async def _restart_session(self) -> None:
        backoff = RECONNECT_INITIAL_S
        while not self._closed:
            try:
                old, self._session = self._session, None
                if old is not None:
                    await old.close()
                await self.start()
                logger.info("deepslate session re-established")
                return
            except Exception as e:
                logger.warning("session restart failed (%s); retrying in %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)

    def _schedule_restart(self) -> None:
        if self._closed:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._restart_session())

    # -- DeviceHandler (device -> bridge) --------------------------------------

    async def on_start(self) -> None:
        logger.info("device session start")

    async def on_wake(self) -> None:
        logger.info("wake: new turn")
        self._suppressed = False
        self._replying = False
        self._up.reset()

    async def on_interrupt(self) -> None:
        logger.info("device interrupt (stop): suppressing model audio until next turn")
        self._suppressed = True

    async def on_flush(self) -> None:
        logger.info("follow-up window expired: suppressing until next turn")
        self._suppressed = True
        self._up.reset()

    async def on_audio(self, pcm16k: bytes) -> None:
        if self._session is None:
            return
        pcm24k = self._up.process(pcm16k)
        if pcm24k:
            await self._session.send_audio(pcm24k, SESSION_SAMPLE_RATE, SESSION_CHANNELS)

    async def on_disconnect(self) -> None:
        self._closed = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
        if self._session is not None:
            await self._session.close()
            self._session = None

    # -- DeepslateSessionListener (Deepslate -> bridge) ------------------------

    async def on_session_initialized(self) -> None:
        logger.info("deepslate session ready")

    async def on_vad_state_event(
        self, from_state: str, to_state: str, session_time_ms: int, packet_id: int
    ) -> None:
        logger.debug("vad %s -> %s", from_state, to_state)
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
        self._replying = False

    async def on_audio_chunk(
        self, pcm_bytes: bytes, sample_rate: int, channels: int, transcript=None
    ) -> None:
        if self._suppressed:
            return
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
        logger.error("deepslate error [%s] %s (trace: %s)", category, message, trace_id)

    async def on_fatal_error(self, e: Exception) -> None:
        logger.error("deepslate session died: %r — recreating", e)
        self._replying = False
        await self._conn.send_phase("idle")
        self._schedule_restart()
