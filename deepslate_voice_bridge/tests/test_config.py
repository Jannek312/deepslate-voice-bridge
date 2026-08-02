import json

from app.config import Settings


def _base_env(tmp_path, monkeypatch, options: dict | None = None):
    opts = tmp_path / "options.json"
    if options is not None:
        opts.write_text(json.dumps(options))
    monkeypatch.setattr("app.config.OPTIONS_PATH", str(opts))
    for var in (
        "DEEPSLATE_VENDOR_ID",
        "DEEPSLATE_ORG_ID",
        "DEEPSLATE_API_KEY",
        "HA_URL",
        "HA_TOKEN",
        "SUPERVISOR_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_loads_addon_options(tmp_path, monkeypatch):
    _base_env(
        tmp_path,
        monkeypatch,
        {
            "vendor_id": "v-1",
            "org_id": "o-1",
            "api_key": "k-1",
            "voice_id": "voice-9",
            "language": "German",
            "follow_up_ms": 5000,
        },
    )
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-tok")
    s = Settings.load()
    assert s.vendor_id == "v-1"
    assert s.voice_id == "voice-9"
    assert s.language == "German"
    assert s.follow_up_ms == 5000
    # defaults preserved for unset options
    assert s.wake_open_delay_ms == 700
    assert s.port == 8080
    # add-on mode: HA via supervisor proxy
    assert s.ha_url == "http://supervisor/core"
    assert s.ha_token == "sup-tok"


def test_env_overrides_options(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch, {"vendor_id": "from-file", "org_id": "o", "api_key": "k"})
    monkeypatch.setenv("DEEPSLATE_VENDOR_ID", "from-env")
    monkeypatch.setenv("HA_URL", "https://home.example.dev")
    monkeypatch.setenv("HA_TOKEN", "llt")
    s = Settings.load()
    assert s.vendor_id == "from-env"
    assert s.ha_url == "https://home.example.dev"
    assert s.ha_token == "llt"


def test_no_options_file_env_only(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch, options=None)
    monkeypatch.setenv("DEEPSLATE_VENDOR_ID", "v")
    monkeypatch.setenv("DEEPSLATE_ORG_ID", "o")
    monkeypatch.setenv("DEEPSLATE_API_KEY", "k")
    monkeypatch.setenv("HA_URL", "https://ha.local")
    monkeypatch.setenv("HA_TOKEN", "t")
    s = Settings.load()
    assert s.org_id == "o"
    assert s.follow_up_ms == 8000


def test_tts_provider_config(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch, {
        "vendor_id": "v", "org_id": "o", "api_key": "k",
        "tts_provider": "elevenlabs", "voice_id": "el-voice",
        "elevenlabs_api_key": "el-key", "elevenlabs_model_id": "eleven_turbo_v2",
        "elevenlabs_stability": 0.4, "background_audio_url": "https://x/a.mp3",
    })
    s = Settings.load()
    assert s.tts_provider == "elevenlabs"
    assert s.elevenlabs_api_key == "el-key"
    assert s.elevenlabs_stability == 0.4
    assert s.elevenlabs_speed == -1.0
    assert s.background_audio_url == "https://x/a.mp3"


def test_build_tts_config_variants(tmp_path, monkeypatch):
    from app.deepslate import build_tts_config
    from deepslate.core import ElevenLabsTtsConfig, HostedTtsConfig

    _base_env(tmp_path, monkeypatch, None)
    s = Settings(voice_id="hosted-v")
    assert isinstance(build_tts_config(s), HostedTtsConfig)

    s = Settings(voice_id="el-v", tts_provider="elevenlabs",
                 elevenlabs_api_key="k", elevenlabs_stability=0.3)
    cfg = build_tts_config(s)
    assert isinstance(cfg, ElevenLabsTtsConfig)
    assert cfg.voice_id == "el-v"
    assert cfg.model_id is None
    assert cfg.voice_settings.stability == 0.3
    assert cfg.voice_settings.speed is None

    assert build_tts_config(Settings()) is None
