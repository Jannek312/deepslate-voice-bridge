"""Bridge settings, loaded from the add-on's /data/options.json with env overrides.

Env overrides make the same code runnable outside the add-on (dev on a laptop):
DEEPSLATE_VENDOR_ID / DEEPSLATE_ORG_ID / DEEPSLATE_API_KEY / HA_URL / HA_TOKEN.
Inside the add-on, HA access defaults to the Supervisor core proxy with the
injected SUPERVISOR_TOKEN.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields

OPTIONS_PATH = "/data/options.json"

_ENV_MAP = {
    "vendor_id": "DEEPSLATE_VENDOR_ID",
    "org_id": "DEEPSLATE_ORG_ID",
    "api_key": "DEEPSLATE_API_KEY",
    "base_url": "DEEPSLATE_BASE_URL",
    "voice_id": "DEEPSLATE_VOICE_ID",
    "ha_url": "HA_URL",
    "ha_token": "HA_TOKEN",
    "port": "BRIDGE_PORT",
    "log_level": "LOG_LEVEL",
}


@dataclass
class Settings:
    vendor_id: str = ""
    org_id: str = ""
    api_key: str = ""
    base_url: str = "https://app.deepslate.eu"
    voice_id: str = ""
    language: str = "auto"
    extra_prompt: str = ""
    output_gain: float = 1.0
    # TTS provider: "hosted" (Deepslate voice, uses voice_id) or "elevenlabs"
    # (uses voice_id as the ElevenLabs voice + the fields below).
    tts_provider: str = "hosted"
    elevenlabs_api_key: str = ""
    elevenlabs_model_id: str = ""
    elevenlabs_stability: float = -1.0        # -1 = provider default
    elevenlabs_similarity_boost: float = -1.0
    elevenlabs_style: float = -1.0
    elevenlabs_speaker_boost: bool = False
    elevenlabs_speed: float = -1.0
    # Ambient audio played on the device's media channel from wake word until
    # the session returns to idle. Empty = disabled.
    background_audio_url: str = ""
    # Tool backend: "lights" (built-in light tools) or "mcp" (Home Assistant's
    # MCP Server integration — full access to everything exposed to Assist).
    tools_mode: str = "lights"
    port: int = 8080
    ha_url: str = ""
    ha_token: str = ""
    follow_up_ms: int = 8000
    follow_up_open_delay_ms: int = 700
    wake_open_delay_ms: int = 700
    playback_prebuffer_ms: int = 150
    log_level: str = "info"

    @classmethod
    def load(cls) -> "Settings":
        values: dict = {}
        try:
            with open(OPTIONS_PATH) as f:
                raw = json.load(f)
            known = {f.name for f in fields(cls)}
            values.update({k: v for k, v in raw.items() if k in known and v is not None})
        except FileNotFoundError:
            pass

        for field_name, env_name in _ENV_MAP.items():
            if os.environ.get(env_name):
                values[field_name] = os.environ[env_name]

        settings = cls(**values)
        settings.port = int(settings.port)
        settings.follow_up_ms = int(settings.follow_up_ms)
        settings.follow_up_open_delay_ms = int(settings.follow_up_open_delay_ms)
        settings.wake_open_delay_ms = int(settings.wake_open_delay_ms)
        settings.playback_prebuffer_ms = int(settings.playback_prebuffer_ms)
        settings.output_gain = float(settings.output_gain)

        # Add-on mode: no explicit HA target means "use the Supervisor proxy".
        if not settings.ha_url:
            settings.ha_url = "http://supervisor/core"
        if not settings.ha_token:
            settings.ha_token = os.environ.get("SUPERVISOR_TOKEN", "")
        return settings
