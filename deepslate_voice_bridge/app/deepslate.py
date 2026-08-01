"""Deepslate session construction: options, VAD, TTS voice, system prompt."""

from __future__ import annotations

from deepslate.core import (
    DeepslateOptions,
    DeepslateSession,
    DeepslateSessionListener,
    HostedTtsConfig,
    VadConfig,
)

from app.config import Settings

# The session's single audio-line rate. The device plays 24 kHz (hardcoded in
# the firmware); mic audio is upsampled 16k→24k before send (app/audio.py).
SESSION_SAMPLE_RATE = 24000
SESSION_CHANNELS = 1


def build_system_prompt(snapshot: dict, settings: Settings) -> str:
    lines = [
        "You are a friendly, snappy smart-home voice assistant running on a "
        "Home Assistant Voice PE speaker, powered by Deepslate.",
        "Your replies are spoken aloud: keep them short, natural and "
        "conversational. Never read out entity IDs, JSON or technical names.",
        "You control the home's lights via the provided tools. Use "
        "control_lights to switch lights and get_lights to check state. "
        "Confirm actions briefly ('Done — bedroom lights are on').",
        "If the user names a room or light that doesn't exist, say what you "
        "know instead of guessing.",
    ]
    if settings.language and settings.language.lower() != "auto":
        lines.append(
            f"LANGUAGE: Always speak and understand {settings.language}. "
            "Never switch language."
        )
    else:
        lines.append("LANGUAGE: Answer in the language the user speaks to you.")

    inventory = ["The home has these areas and lights:"]
    for area in snapshot["areas"]:
        names = ", ".join(light["name"].strip() for light in area["lights"])
        inventory.append(f"- {area['name']}: {names}")
    lines.append("\n".join(inventory))

    if settings.extra_prompt:
        lines.append(settings.extra_prompt)
    return "\n\n".join(lines)


def create_session(
    settings: Settings, system_prompt: str, listener: DeepslateSessionListener
) -> DeepslateSession:
    options = DeepslateOptions(
        vendor_id=settings.vendor_id,
        organization_id=settings.org_id,
        api_key=settings.api_key,
        system_prompt=system_prompt,
    )
    # VAD tuned for a far-field smart speaker: slightly stricter confidence
    # than the SDK default and a longer stop window so natural mid-sentence
    # pauses don't cut the user off. Revisit after live testing.
    vad = VadConfig(
        confidence_threshold=0.6,
        min_volume=0.05,
        start_duration_ms=100,
        stop_duration_ms=700,
        backbuffer_duration_ms=1000,
    )
    tts = HostedTtsConfig(voice_id=settings.voice_id) if settings.voice_id else None
    return DeepslateSession.create(
        options,
        vad_config=vad,
        tts_config=tts,
        user_agent="DeepslateVoiceBridge",
        listener=listener,
    )
