# Deepslate Voice Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Home Assistant add-on that bridges the Voice PE thin-client firmware to Deepslate Realtime (Opal S2S) with client-side tool calls that control Home Assistant lights.

**Architecture:** Single async Python service. `device_server` speaks the `va_client` LAN WebSocket protocol (binary PCM + compact JSON control frames). `deepslate_client` speaks the Deepslate Realtime protobuf protocol over WSS. `bridge` wires one device connection to one Deepslate session and maps events; `tools`/`ha_client` execute light tool calls against the HA API.

**Tech Stack:** Python 3.12, `websockets`, `aiohttp`, `protobuf` (pre-generated `realtime_pb2.py`), `pytest`/`pytest-asyncio`. Packaged as a Home Assistant add-on (Dockerfile + config.yaml).

## Global Constraints

- Device→bridge JSON control frames arrive as TEXT; bridge→device JSON MUST be compact (`json.dumps(obj, separators=(",", ":"))`) — the firmware substring-matches `"value":"<phase>"`.
- Device audio up: raw PCM16 mono 16000 Hz binary frames. Device audio down: raw PCM16 mono 24000 Hz binary frames.
- Deepslate endpoint: `wss://app.deepslate.eu/api/v1/vendors/{vendor_id}/organizations/{org_id}/realtime`, header `Authorization: Bearer <api_key>`.
- Every Deepslate `ToolCallRequest` MUST get a `ToolCallResponse`, even on failure.
- Secrets never land in git (`todo.md` is gitignored; tests use fake creds).
- Timing defaults (battle-tested by the reference add-on): `follow_up_ms=8000`, `follow_up_open_delay_ms=700`, `wake_open_delay_ms=700`, `playback_prebuffer_ms=150`.

## Protocol contracts (single source of truth)

### Device (va_client) protocol
| Direction | Frame | Meaning |
|---|---|---|
| dev→srv TEXT | `{"type":"start"}` | once per WS connection |
| dev→srv TEXT | `{"type":"wake"}` | every wake word trigger |
| dev→srv TEXT | `{"type":"interrupt"}` | user said "stop" |
| dev→srv TEXT | `{"type":"flush"}` | follow-up window timed out mid-stream; drop uncommitted input |
| dev→srv TEXT | `{"type":"ping"}` | keepalive → reply `{"type":"pong"}` |
| dev→srv BIN | PCM16 mono 16 kHz | mic audio (only while device mic gate open) |
| srv→dev TEXT | `{"type":"hello","audio_out":"pcm","follow_up_ms":8000,"follow_up_open_delay_ms":700,"wake_open_delay_ms":700,"playback_prebuffer_ms":150}` | on connect |
| srv→dev TEXT | `{"type":"phase","value":"listening"\|"thinking"\|"replying"\|"idle"}` | LED/mic state machine |
| srv→dev BIN | PCM16 mono 24 kHz | reply audio |

### Deepslate session config
`InitializeSessionRequest`: input line 16000/1/SIGNED_16_BIT; output line 24000/1/SIGNED_16_BIT (no resampling anywhere); `vad_configuration` (confidence 0.6, min_volume 0.05, start 96ms, stop 700ms, backbuffer 1s — tune live); `inference_configuration.system_prompt` built at session start from HA areas/lights; `tts_configuration.hosted.voice_ref.voice_id` from config (mode HIGH_QUALITY); `supports_playback_reporting=false` (device reports no playback position; server estimates). Then `UpdateToolDefinitionsRequest` immediately after init. Audio sent as `UserInput{packet_id: monotonic, mode: <UserInput mode decided in Task 4 from deepslate-pipecat reference>, audio_data}`.

### Event mapping (bridge core)
| Trigger | Action |
|---|---|
| device connects | send `hello`; ensure Deepslate session open (create if needed) |
| device `wake` | mark turn boundary; unsuppress output; phase stays device-driven (`listening` locally) |
| device mic PCM | forward as `UserInput` (only when Deepslate session ready; else drop) |
| device `interrupt` | suppress forwarding of current turn's remaining audio until next `ResponseBegin` (device already silenced locally) |
| device `flush` | mark turn boundary; send 800 ms of PCM16 silence to let server VAD close any dangling segment (hook: revisit live) |
| DS `SessionReady` | log ready; allow audio forwarding |
| DS `VadStateEvent` → `SPEECH` | (barge-in signal comes via PlaybackClearBuffer; log only) |
| DS `VadStateEvent` → from SPEECH to SPEECH_ENDING/SILENCE | phase `thinking` |
| DS `PlaybackClearBuffer` | phase `listening` (firmware flushes its playback queue on that transition) |
| DS `ResponseBegin` | clear interrupt suppression |
| DS first `ModelAudioChunk` of turn | phase `replying`, then forward chunk bytes |
| DS further `ModelAudioChunk` | forward bytes (unless suppressed) |
| DS `ResponseEnd` | phase `idle` (device runs its own follow-up window) |
| DS `ToolCallRequest` | execute via tools.py; always answer `ToolCallResponse` |
| DS `SessionErrorNotification` / socket close | phase `idle`; reconnect Deepslate with backoff; device socket stays up |
| device socket close | keep Deepslate session for 60 s grace, then close |

## File Structure

```
deepslate_voice_bridge/           # HA add-on directory
  config.yaml                     # add-on manifest + options schema
  Dockerfile
  DOCS.md
  run.sh
  app/
    __init__.py
    config.py                     # Settings: env vars + /data/options.json
    realtime.proto                # vendored Deepslate proto
    realtime_pb2.py               # generated (checked in; regen script in scripts/)
    ha_client.py                  # HAClient: REST + template API
    tools.py                      # tool defs + ToolExecutor (lights)
    deepslate_client.py           # DeepslateSession (protobuf WS client)
    device_server.py              # DeviceServer + DeviceConnection (va_client WS)
    bridge.py                     # Bridge: event mapping + reconnect
    main.py                       # entrypoint
  tests/
    test_config.py
    test_ha_client.py
    test_tools.py
    test_deepslate_client.py
    test_device_server.py
    test_bridge.py
repository.json                   # HA add-on repo manifest
firmware/deepslate-voice.yaml     # per-device ESPHome stub (remote package)
scripts/gen_proto.sh
```

---

### Task 1: Scaffolding + protobuf generation

**Files:** Create `deepslate_voice_bridge/app/__init__.py`, `pyproject.toml` (uv-managed, deps: websockets>=13, aiohttp>=3.10, protobuf>=5; dev: pytest, pytest-asyncio, grpcio-tools), `scripts/gen_proto.sh`, vendored `app/realtime.proto`, generated `app/realtime_pb2.py`.

**Interfaces produced:** importable `app.realtime_pb2` with `ServiceBoundMessage`, `ClientBoundMessage`, all messages from the proto.

- [ ] Step 1: `uv init`-style pyproject; add deps; copy proto from scratchpad.
- [ ] Step 2: Write failing test `test_proto_roundtrip`:
```python
from app import realtime_pb2 as pb
def test_proto_roundtrip():
    msg = pb.ServiceBoundMessage(
        user_input=pb.UserInput(packet_id=7, mode=pb.QUEUE,
                                audio_data=pb.AudioData(data=b"\x00\x01")))
    parsed = pb.ServiceBoundMessage.FromString(msg.SerializeToString())
    assert parsed.user_input.packet_id == 7
```
- [ ] Step 3: `scripts/gen_proto.sh` runs `python -m grpc_tools.protoc -I app --python_out=app app/realtime.proto`; run it; test passes.
- [ ] Step 4: Commit.

### Task 2: Settings (config.py)

**Interfaces produced:** `Settings` dataclass: `vendor_id, org_id, api_key, voice_id, language ("auto"), extra_prompt (""), port (8080), ha_url, ha_token, follow_up_ms (8000), follow_up_open_delay_ms (700), wake_open_delay_ms (700), playback_prebuffer_ms (150), log_level ("info")`; classmethod `Settings.load()` reads `/data/options.json` if present (add-on), then env-var overrides (`DEEPSLATE_VENDOR_ID` etc.); `ha_url`/`ha_token` default to `http://supervisor/core` + `SUPERVISOR_TOKEN` env when unset.

- [ ] Test: options.json parsing, env override, supervisor defaults. Implement. Commit.

### Task 3: HA client (ha_client.py)

**Interfaces produced:** `class HAClient(base_url, token)`: `async call_service(domain, service, data) -> dict`; `async render_template(template: str) -> str`; `async lights_snapshot() -> dict` returning `{areas: [{id, name, lights: [{entity_id, name, state}]}]}` built via the template API (`{{ areas() }}`, `{{ area_name(a) }}`, `{{ area_entities(a) | select('match','light\\.') }}`, `{{ state_attr(e,'friendly_name') }}`, `{{ states(e) }}` — one combined Jinja template returning JSON via `| tojson`); `async close()`.

- [ ] Test with `aiohttp.test_utils` fake HA server asserting auth header, `POST /api/template` body, `POST /api/services/light/turn_on` passthrough, snapshot parsing. Implement. Commit.

> **DEVIATION (recorded during execution):** Task 4 is implemented with the official
> `deepslate-core` SDK (found on PyPI during Task 4 Step 1) instead of a hand-rolled
> protobuf client. It provides `DeepslateSession` + `DeepslateSessionListener` with
> exactly the events the bridge needs, internal reconnect-with-backoff, and buffered
> sends. Consequences: (a) vendored proto/generated pb2 removed; (b) the session runs
> at 24 kHz both directions (SDK uses one audio line config), so the bridge upsamples
> mic audio 16k→24k in `app/audio.py` (same as the OpenAI reference add-on does);
> (c) audio `UserInput.mode` follows the SDK default `IMMEDIATE`; (d) tool defs use
> the SDK's `FunctionToolDict` shape `{"type":"function","function":{name,description,parameters}}`.

### Task 4: Deepslate client (deepslate_client.py)

**Interfaces produced:**
```python
class DeepslateSession:
    def __init__(self, settings: Settings, system_prompt: str, tool_definitions: list[dict]): ...
    async def connect(self) -> None            # WS connect + Initialize + UpdateToolDefinitions
    async def events(self) -> AsyncIterator[pb.ClientBoundMessage]   # yields parsed messages
    async def send_audio(self, pcm: bytes) -> None    # wraps UserInput, monotonic packet_id
    async def send_tool_response(self, call_id: str, result: str) -> None
    async def close(self) -> None
    url property: wss://app.deepslate.eu/api/v1/vendors/{v}/organizations/{o}/realtime (host overridable via DEEPSLATE_WS_URL for tests)
```
Tool definitions dict → `ToolDefinition` with `google.protobuf.struct_pb2.Struct` params via `json_format.ParseDict`.

- [ ] Step 1: Check `deepslate-pipecat` source (`pip download deepslate-pipecat` into scratchpad) for the UserInput `mode` its own plugin uses + whether `HostedVoiceRef` allows empty voice_id; adopt the same. Record decision in code comment.
- [ ] Step 2: Tests against an in-process `websockets.serve` fake: first frame is InitializeSessionRequest with 16k in/24k out; second is UpdateToolDefinitionsRequest; `send_audio` produces UserInput with incrementing packet_id; server-pushed ToolCallRequest surfaces via `events()`; `send_tool_response` framing.
- [ ] Step 3: Implement; tests pass. Commit.

### Task 5: Tools (tools.py)

**Interfaces produced:** `TOOL_DEFINITIONS: list[dict]` (JSON-schema, OpenAI-function-like) for `control_lights(area?, name?, action: "on"|"off", brightness_pct?, color?)` and `get_lights(area?)`; `class ToolExecutor(ha: HAClient, snapshot)` with `async execute(name: str, params: dict) -> str` (returns human/JSON string; never raises — errors become the result string). Area/name resolution: case-insensitive containment match against snapshot area names and light friendly names; unresolved → helpful error string listing valid names.

- [ ] Tests: on/off by area, by name, brightness/color pass-through (`brightness_pct`, `color_name` in service data), unknown area error string, `get_lights` output, HA exception → error string. Implement. Commit.

### Task 6: Device server (device_server.py)

**Interfaces produced:**
```python
class DeviceConnection:   # one device socket
    async def send_phase(self, value: str)      # compact JSON!
    async def send_audio(self, pcm24k: bytes)
    async def send_hello(self)                  # from Settings timing fields
class DeviceServer:
    def __init__(self, settings, handler_factory)  # handler_factory(conn) -> DeviceHandler
    async def start(self); async def stop()
# DeviceHandler protocol (implemented by Bridge in Task 7):
#   async on_wake(); on_start(); on_interrupt(); on_flush(); on_audio(bytes); on_disconnect()
```

- [ ] Tests with a real `websockets` client against the started server: hello arrives first and is compact JSON with all four timing keys; text messages dispatch to the right handler method; binary → `on_audio`; `ping`→`pong`; phase send is compact (assert `'"value":"listening"' in raw`). Implement. Commit.

### Task 7: Bridge (bridge.py)

**Interfaces consumed:** all of Tasks 3–6. **Produces:** `class Bridge` implementing DeviceHandler; owns DeepslateSession lifecycle (lazy-connect, exponential backoff 1→30 s, re-init with fresh HA snapshot + system prompt on each reconnect); implements the full Event mapping table above; `build_system_prompt(snapshot, settings) -> str` (persona, response-style-for-voice rules, language directive, area/light inventory, tool usage guidance).

- [ ] Tests (the big ones), using fake device + fake Deepslate server end-to-end:
  - wake→audio→VAD stop event → device got phase `thinking`; audio chunk → `replying` + bytes; ResponseEnd → `idle`.
  - ToolCallRequest for control_lights → fake HA got service call → ToolCallResponse sent.
  - interrupt mid-reply → subsequent chunks of that turn NOT forwarded; next ResponseBegin resumes.
  - Deepslate socket dies → device phase `idle`, reconnect attempted, device socket stays open.
  - tool failure → ToolCallResponse still sent with error text.
- [ ] Implement; tests pass. Commit.

### Task 8: Entrypoint (main.py) + dev run

- [ ] `main()`: Settings.load → HAClient → DeviceServer(Bridge factory) → run until SIGTERM; structured logging with log_level. Smoke test: process starts with env config and fake HA, binds port. Commit.

### Task 9: Add-on packaging

- [ ] `config.yaml` (name "Deepslate Voice Bridge", slug `deepslate_voice_bridge`, `host_network: true`, options+schema mirroring Settings; `hassio_api: false`, `homeassistant_api: true` so SUPERVISOR_TOKEN can call core API), Dockerfile (python:3.12-slim, uv sync or pip install), `run.sh`, `repository.json`, DOCS.md (install + firmware pointer). Verify: `docker build` succeeds locally. Commit.

### Task 10: Firmware stub + live end-to-end

- [ ] `firmware/deepslate-voice.yaml`: stub with `packages: github://xandervanerven/home-assistant-voice-pe/home-assistant-voice.realtime.yaml@main`, substitutions for name/friendly_name/wifi/api key/`va_url` (dev: Mac LAN IP; prod: homeassistant.local). NOTE: needs the user's Wi-Fi credentials — ask when reaching this task (batch with flash confirmation).
- [ ] Run bridge on Mac with dev env (HA URL https://home.jannek.dev + long-lived token, Deepslate creds from todo.md); flash device via USB (`esphome run`); live test: wake → "turn on the bedroom lights" → lights + spoken confirmation; barge-in; follow-up.
- [ ] Fix what live testing surfaces (VAD tuning, flush-silence behavior, voice_id).

## Self-review checklist findings
- Spec coverage: all spec sections map to tasks (arch→1-8, tools→5, config→2/9, errors→7, testing/rollout→tasks' tests + 10). Gaps: none.
- The UserInput `mode` and empty-voice_id questions are explicitly resolved in Task 4 Step 1 (from the deepslate-pipecat reference source), not left open.
- Type consistency: DeviceHandler method names consistent between Tasks 6 and 7; `Settings` fields between 2, 6, 9.
