# Deepslate Voice — Home Assistant Voice PE ↔ Deepslate Realtime

Talk to a Home Assistant Voice PE and have the whole conversation run through
[Deepslate](https://docs.deepslate.eu/) Opal (speech-to-speech), with your
Home Assistant lights controlled via tool calls.

```
Voice PE ──16k PCM / LAN WS──▶ Deepslate Voice Bridge ──protobuf WSS──▶ Deepslate Realtime (Opal)
  ▲ wake word, AEC, LEDs        (HA add-on, this repo)──HTTP──▶ Home Assistant (lights)
  └──────24k PCM + phases───────────────┘
```

## Repository layout

- `deepslate_voice_bridge/` — the Home Assistant **add-on** (Python bridge service + tests)
- `firmware/` — per-device ESPHome stub for the thin-client firmware
  (remote package from [xandervanerven/home-assistant-voice-pe](https://github.com/xandervanerven/home-assistant-voice-pe))
- `repository.json` — makes this repo installable as a HA add-on repository
- `docs/superpowers/` — design spec and implementation plan

## Quick start

**Dev (bridge on your laptop):**

```sh
uv sync
DEEPSLATE_VENDOR_ID=… DEEPSLATE_ORG_ID=… DEEPSLATE_API_KEY=… DEEPSLATE_VOICE_ID=… \
HA_URL=https://your-ha HA_TOKEN=… \
uv run python -m app.main   # from deepslate_voice_bridge/
```

Flash the device with `va_url` pointing at your laptop:

```sh
cp firmware/secrets.yaml.example firmware/secrets.yaml  # fill in Wi-Fi
uvx esphome run firmware/deepslate-voice.yaml --device /dev/cu.usbmodem*
```

**Prod:** add this repo as an add-on repository in HA (Settings → Add-ons →
Add-on store → ⋮ → Repositories), install "Deepslate Voice Bridge", fill in
the Deepslate credentials, and set the firmware `va_url` to
`ws://homeassistant.local:8080/`.

## Tests

```sh
uv run pytest
```
