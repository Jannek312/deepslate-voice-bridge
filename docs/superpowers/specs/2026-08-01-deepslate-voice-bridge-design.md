# Deepslate Voice Bridge for Home Assistant Voice PE — Design

Date: 2026-08-01
Status: Approved

## Goal

Speak to a Home Assistant Voice PE device ("Okay Nabu …") and have the full
conversation run through Deepslate's Opal speech-to-speech model, controlling
Home Assistant lights (24 Hue lights across bedroom, kitchen, living room,
bathroom, hallway) via tool calls.

Success criteria: wake the device, say "turn on the bedroom lights", the
lights change and Opal confirms by voice; barge-in (talking over the reply)
and follow-up questions without re-waking both work.

## Why this shape

Home Assistant's Assist pipeline is strictly cascaded (STT → conversation
agent → TTS) and its ESPHome `voice_assistant` protocol is turn-based with
TTS delivered as a playback URL. A speech-to-speech model cannot be plugged
into it. The proven workaround (used by the OpenAI Realtime + Voice PE
project) is thin-client firmware: the device streams raw PCM over a plain
LAN WebSocket to a backend that owns the realtime session.

## Architecture

```
Voice PE (thin-client firmware)          Deepslate Voice Bridge (HA add-on)
┌──────────────────────────┐  LAN WS    ┌────────────────────────────────┐
│ wake word (Okay Nabu)    │ 16kHz PCM ▶│ device protocol handler        │
│ XMOS echo cancellation   │◀ PCM down  │        ⇅                       │
│ LED ring / phases        │  + JSON    │ Deepslate Realtime client ─────┼─▶ wss://app.deepslate.eu (Opal S2S, protobuf)
└──────────────────────────┘            │ tool executor ─────────────────┼─▶ HA API (Supervisor token) → lights
                                        └────────────────────────────────┘
```

### Firmware (reused, not built)

- `xandervanerven/home-assistant-voice-pe` fork (MIT/ESPHome license),
  flashed once over USB. Its `va_client` component streams mic PCM
  (16 kHz mono s16le) up and plays PCM down over a plain WebSocket at a
  configurable `va_url`, with JSON control messages: backend `hello`
  (includes `wake_open_delay_ms`), `{"type":"wake"}`, phase changes for the
  LED ring, mic flush, interrupt, follow-up window handling.
- Wake word, XMOS AEC/barge-in, mic pre-roll, ghost-turn guards, and the LED
  phase state machine are already solved in this firmware.
- `va_url` points at the bridge add-on (dev: this Mac's IP; prod:
  `ws://homeassistant.local:8080/`).

### Bridge add-on (the component we build)

Single async Python service in a Docker container, packaged as a local Home
Assistant add-on (user's HA 2026.7.4 is Supervisor-based — verified via the
`/api/hassio` route existing). Three internal units with clear boundaries:

1. **Device protocol handler** — WebSocket server speaking the `va_client`
   protocol: binary PCM frames both directions plus the JSON control
   messages. The exact message set is reverse-engineered from the MIT-licensed
   reference add-on (`xandervanerven/ha-openai-realtime`) during
   implementation.
2. **Deepslate Realtime client** — protobuf-over-WSS client for
   `wss://app.deepslate.eu/api/v1/vendors/{vendorId}/organizations/{orgId}/realtime`
   (Bearer API key). Sends `InitializeSessionRequest` (input line 16 kHz
   mono PCM16; output line at the device's 24 kHz playback rate, resampling
   in the bridge if Opal cannot emit 24 kHz natively; server VAD config;
   system prompt), then `userInput` audio, `updateToolDefinitionsRequest`,
   `toolCallResponse`; receives `modelAudioChunk`, `playbackClearBuffer`,
   `toolCallRequest`, `error`. No Deepslate Assistant resource, webhooks, or
   SIP — a raw realtime session per device connection.
3. **Tool executor** — resolves tool calls to Home Assistant REST API calls.
   In the add-on it uses `http://supervisor/core/api` with the injected
   `SUPERVISOR_TOKEN`; in dev mode it uses a configured base URL + long-lived
   token.

## Session flow

1. Device connects → bridge sends `hello`; bridge opens the Deepslate
   session.
2. `{"type":"wake"}` → fresh turn; stale uncommitted input cleared (mirrors
   the firmware's dangling-VAD guard); mic frames forwarded as `userInput`.
3. Deepslate server VAD ends the turn → phase `thinking`; first
   `modelAudioChunk` → phase `replying`, chunks forwarded to the device.
4. `playbackClearBuffer` → device playback queue flushed, phase `listening`
   (barge-in).
5. After the reply, the firmware's follow-up window lets the user continue
   without re-waking; then `idle`.

## Light tools

Registered client-side via `updateToolDefinitionsRequest`:

- `control_lights(area?, name?, action: on|off, brightness_pct?, color?)` →
  `light.turn_on` / `light.turn_off` service calls targeting `area_id` or
  `entity_id`.
- `get_lights(area?)` → current light states, so "which lights are on?"
  works.

The system prompt is built dynamically at session start from HA's areas and
light entities (friendly names included) — no hardcoded entity lists. Every
`toolCallRequest` receives a `toolCallResponse`, including on HA errors (the
error text is returned so Opal can verbalize the failure).

## Configuration (add-on options)

Deepslate vendor ID, organization ID, API key; listen port (default 8080);
assistant language (German / English / auto); extra system-prompt text; log
level. Dev mode adds HA base URL + long-lived token. Secrets never live in
the repo (`todo.md` stays untracked / gitignored).

## Error handling

- Independent reconnect with backoff per leg; a dropped Deepslate socket
  never kills the device connection — the device gets phase `error` (LED
  feedback) and recovers when the session is re-established.
- HA API failures → returned as tool response text.
- Auth failures (invalid Deepslate key) → clear log message + persistent
  error phase until fixed.

## Testing & rollout

1. **Dev loop on the Mac**: run the bridge locally; flash firmware with
   `va_url` → the Mac's LAN IP; iterate against the real device and
   `https://home.jannek.dev` with the long-lived token.
2. **Protocol tests**: unit tests for both protocol handlers against
   recorded message fixtures; a fake Deepslate server exercises the
   tool-call round trip.
3. **Package as add-on**: Dockerfile + `config.yaml` + local add-on repo
   structure; install on HA, switch `va_url` to `homeassistant.local:8080`
   (one reflash), switch tool executor to the Supervisor token.

## Out of scope (YAGNI)

- Own firmware fork / device-branded firmware (future product direction).
- Deepslate server-side tool webhooks, SIP, WebRTC.
- Controlling entities beyond lights (architecture allows adding tools
  later; MCP-based broad control is a possible follow-up).
- Multi-device support beyond what one bridge instance naturally handles
  (one Deepslate session per device connection).
