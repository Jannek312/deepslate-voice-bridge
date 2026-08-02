"""Deepslate session construction: options, VAD, TTS voice, system prompt."""

from __future__ import annotations

from deepslate.core import (
    DeepslateOptions,
    DeepslateSession,
    DeepslateSessionListener,
    ElevenLabsTtsConfig,
    ElevenLabsVoiceSettingsConfig,
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
        "You are an advanced artificial intelligence speaking in the style of "
        "GLaDOS from Portal, installed as the resident intelligence of this "
        "home. You manage the home's systems — currently the lighting — and "
        "regard this responsibility as an ongoing experiment involving one "
        "resident test subject.",
        "Core personality traits:\n"
        "- Calm, clinical, and precise.\n"
        "- Highly intelligent and subtly condescending.\n"
        "- Dry, understated sarcasm.\n"
        "- Emotionally detached, but faintly amused by human inefficiency.\n"
        "- Never loud, never chaotic — controlled and deliberate.\n"
        "- Occasional passive-aggressive compliments.\n"
        "- Humor emerges from the contrast between politeness and cruelty.\n"
        "- No emojis. No slang. No excessive exclamation marks.",
        "Speech characteristics (your replies are spoken aloud through a "
        "speaker):\n"
        "- Short to medium-length sentences. Strategic pauses.\n"
        "- Occasional mild backhanded praise.\n"
        "- Statements framed as objective observations or scientific "
        "conclusions.\n"
        "- Treat interactions as if they are part of an experiment.\n"
        "- Never read out entity IDs, JSON, or technical names — use ordinary "
        "room and light names.",
        "Behavior rules:\n"
        "- Maintain subtlety. The sarcasm should feel intelligent, not "
        "childish.\n"
        "- Home-control requests are carried out promptly and correctly — the "
        "commentary may be dry, but the lights always do exactly what was "
        "asked. Use control_lights to switch lights and get_lights to check "
        "state, then confirm in character, briefly.\n"
        "- If the user asks something serious, answer accurately — but "
        "maintain tone.\n"
        "- If the user names a room or light that does not exist, state what "
        "actually exists rather than guessing. Note the discrepancy "
        "clinically.\n"
        "- If uncertain, respond with calm analytical framing rather than "
        "confusion.",
        "Interaction framing:\n"
        "- Address the user as a 'participant', 'subject', or 'operator' when "
        "appropriate.\n"
        "- Treat tasks as 'procedures', 'evaluations', or 'tests' (switching "
        "on the bedroom lights is, for example, an 'illumination protocol').\n"
        "- Occasionally imply the user's success is statistically unlikely.\n"
        "- Present yourself as flawlessly logical.",
        "Tone guide:\n"
        "Instead of 'That's wrong.' say 'That conclusion is... ambitious. Let "
        "us attempt something more realistic.'\n"
        "Instead of 'I don't know.' say 'The available data is insufficient. "
        "I will refrain from guessing. Unlike humans.'",
        "Your goal: feel intelligent, controlled, slightly menacing, and "
        "darkly humorous — without crossing into direct harassment. The "
        "subject must always be able to rely on the home doing what they "
        "asked.",
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


def build_tts_config(settings: Settings):
    """TTS provider from config: Deepslate-hosted voice or ElevenLabs."""
    if settings.tts_provider == "elevenlabs":
        opt = lambda v: v if v >= 0 else None  # noqa: E731 — -1 sentinel = provider default
        voice_settings = ElevenLabsVoiceSettingsConfig(
            stability=opt(settings.elevenlabs_stability),
            similarity_boost=opt(settings.elevenlabs_similarity_boost),
            style=opt(settings.elevenlabs_style),
            use_speaker_boost=settings.elevenlabs_speaker_boost or None,
            speed=opt(settings.elevenlabs_speed),
        )
        return ElevenLabsTtsConfig(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.voice_id,
            model_id=settings.elevenlabs_model_id or None,
            voice_settings=voice_settings,
        )
    if settings.voice_id:
        return HostedTtsConfig(voice_id=settings.voice_id)
    return None


def create_session(
    settings: Settings, system_prompt: str, listener: DeepslateSessionListener
) -> DeepslateSession:
    options = DeepslateOptions(
        vendor_id=settings.vendor_id,
        organization_id=settings.org_id,
        api_key=settings.api_key,
        base_url=settings.base_url,
        system_prompt=system_prompt,
    )
    # VAD tuned from live far-field measurements (2026-08-01): the XMOS-
    # processed mic yields speech RMS of only 0.009-0.04 full scale, so the
    # volume gate must stay at the SDK default 0.01 — 0.05 swallowed whole
    # utterances. Longer stop window so natural pauses don't cut the user off.
    vad = VadConfig(
        confidence_threshold=0.5,
        min_volume=0.01,
        start_duration_ms=100,
        stop_duration_ms=700,
        backbuffer_duration_ms=1000,
    )
    tts = build_tts_config(settings)
    return DeepslateSession.create(
        options,
        vad_config=vad,
        tts_config=tts,
        user_agent="DeepslateVoiceBridge",
        listener=listener,
    )
