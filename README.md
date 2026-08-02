# Deepslate Voice — Home Assistant Voice PE ↔ Deepslate Realtime

Talk to a Home Assistant Voice PE and have the whole conversation run through [Deepslate](https://docs.deepslate.eu/) Opal (speech-to-speech), with your Home Assistant lights controlled via tool calls.

```
Voice PE ──── 16 kHz PCM / LAN WebSocket ───▶ Deepslate Voice Bridge ── protobuf WSS ──▶ Deepslate Realtime (Opal)
   ▲                                            (HA add-on, this repo)
   └──── 24 kHz PCM + phase messages ────────────────┘      └────────── HTTP ──▶ Home Assistant (lights)
```

The device keeps wake word detection, echo cancellation and the LED ring on-device (thin-client firmware). The bridge holds one Deepslate Realtime session per conversation — opened on wake word, closed by Deepslate's inactivity timeout — and executes `control_lights` / `get_lights` tool calls against the Home Assistant API. The area and light inventory is read from Home Assistant at session start, so there is nothing to configure per entity.

## Repository layout

- `deepslate_voice_bridge/` — the Home Assistant **add-on** (Python bridge service + tests)
- `firmware/` — ESPHome config for the device: `deepslate-voice.yaml` (per-device stub) + `thin-client.yaml` (vendored thin-client firmware, based on [xandervanerven/home-assistant-voice-pe](https://github.com/xandervanerven/home-assistant-voice-pe))
- `repository.json` — makes this repo installable as a Home Assistant add-on repository
- `docs/superpowers/` — design spec and implementation plan

## Installation (Home Assistant)

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories → add `https://github.com/Jannek312/deepslate-voice-bridge`
2. Install **Deepslate Voice Bridge**
3. Configure it: `vendor_id`, `org_id`, `api_key`, `base_url` (e.g. `https://app.deepslate.eu`), `voice_id` (a Deepslate hosted voice), `language`
4. Flash the firmware with `va_url: ws://homeassistant.local:8080/` (see below)

## Firmware

```sh
cp firmware/secrets.yaml.example firmware/secrets.yaml   # fill in Wi-Fi + keys
uvx esphome run firmware/deepslate-voice.yaml --device /dev/cu.usbmodem*
```

Set the `va_url` substitution in `firmware/deepslate-voice.yaml` to wherever the bridge runs before flashing.

## Development (bridge on your machine)

```sh
uv sync
cd deepslate_voice_bridge
DEEPSLATE_VENDOR_ID=… DEEPSLATE_ORG_ID=… DEEPSLATE_API_KEY=… DEEPSLATE_BASE_URL=… DEEPSLATE_VOICE_ID=… \
HA_URL=https://your-ha HA_TOKEN=… \
uv run python -m app.main
```

Point the firmware's `va_url` at your machine's LAN IP and reflash.

## Tests

```sh
uv run pytest
```
