# Deepslate Voice Bridge

Connects a **Home Assistant Voice PE** running the thin-client firmware to
**Deepslate Realtime** (Opal speech-to-speech). The full conversation runs
through Deepslate; lights are controlled through tool calls against the Home
Assistant API.

## Requirements

1. A Voice PE flashed with the thin-client firmware (see `firmware/` in this
   repository) with its `va_url` pointing at this add-on:
   `ws://homeassistant.local:8080/` (or this host's IP).
2. Deepslate credentials: vendor ID, organization ID, API key, and a hosted
   voice ID.

## Options

| Option | Description |
|---|---|
| `vendor_id` / `org_id` / `api_key` | Deepslate Realtime credentials |
| `tts_provider` | `hosted` (Deepslate voice) or `elevenlabs` |
| `voice_id` | Voice for TTS output: Deepslate hosted voice id, or the ElevenLabs voice id when provider is elevenlabs |
| `elevenlabs_api_key` / `elevenlabs_model_id` | ElevenLabs credentials (provider `elevenlabs` only) |
| `elevenlabs_stability` / `_similarity_boost` / `_style` / `_speed` | ElevenLabs voice settings, `-1` = provider default |
| `elevenlabs_speaker_boost` | Boost similarity to the original speaker |
| `background_audio_url` | Ambient audio (mp3/flac/stream URL) played on the device's media channel from wake word until the session ends; loops, ducks 12 dB while the assistant speaks. Empty = off |
| `language` | `auto` or a fixed language, e.g. `German`, `English` |
| `extra_prompt` | Appended to the generated system prompt |
| `port` | LAN WebSocket port the Voice PE connects to (default 8080) |
| `follow_up_ms` | Mic-open window after a reply for follow-up questions (0 = off) |
| `follow_up_open_delay_ms` / `wake_open_delay_ms` | Echo guards; keep defaults unless you see ghost turns |
| `playback_prebuffer_ms` | Device-side jitter buffer before playback starts |

The area/light inventory is read from Home Assistant automatically at session
start — no entity configuration needed. Lights are matched by area name or
friendly name.

## How it works

Voice PE (wake word + echo cancellation on-device) streams 16 kHz PCM over a
LAN WebSocket to this add-on. The add-on runs a Deepslate Realtime session
(24 kHz, server-side VAD) and answers tool calls (`control_lights`,
`get_lights`) via the Supervisor's Home Assistant API. Barge-in, follow-up
windows and the LED phase ring are driven by the firmware + phase messages.
