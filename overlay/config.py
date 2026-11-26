"""File paths, model profiles and config.json loading with validation."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from overlay.core.regions import validate_region

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
ENV_PATH = APP_DIR / ".env"
ENC_KEY_PATH = APP_DIR / "api_key.dpapi"
LOG_PATH = APP_DIR / "log.txt"

log = logging.getLogger(__name__)

MODEL_PROFILES: dict[str, dict] = {
    "gpt_mini": {"label": "OpenAI · GPT-5.4 mini", "model": "gpt-5.4-mini", "provider": "openai", "fast": False},
    "gpt_quality": {"label": "OpenAI · GPT-5.5", "model": "gpt-5.5", "provider": "openai", "fast": False},
    "sonnet": {
        "provider": "anthropic",
        "label": "Anthropic · Sonnet 5.5",
        "model": "claude-sonnet-5-5",
        "fast": False,
        "fallbacks": True,
    },
    "opus": {
        "provider": "anthropic",
        "label": "Anthropic · Opus 5.5",
        "model": "claude-opus-5-5",
        "fast": False,
        "fallbacks": True,
    },
    "opus_fast": {
        "provider": "anthropic",
        "label": "Anthropic · Opus 5.5 ⚡",
        "model": "claude-opus-5-5",
        "fast": True,
        "fallbacks": True,
    },
    # Haiku has no server-side refusal fallback.
    "haiku": {
        "provider": "anthropic",
        "label": "Anthropic · Haiku 5.5",
        "model": "claude-haiku-5-5",
        "fast": False,
        "fallbacks": False,
    },
}

ANTHROPIC_MODE_PRESETS: dict[str, tuple[str, str]] = {
    "quick": ("sonnet", "low"),
    "interview": ("sonnet", "low"),
    "explain": ("sonnet", "medium"),
    "solve": ("opus", "high"),
}

MODE_PRESETS: dict[str, tuple[str, str]] = {
    "quick": ("gpt_mini", "low"),
    "interview": ("gpt_mini", "low"),
    "explain": ("gpt_mini", "medium"),
    "solve": ("gpt_quality", "high"),
}


def provider_for_model(key: str) -> str:
    return MODEL_PROFILES.get(key, MODEL_PROFILES["gpt_mini"])["provider"]


def mode_preset(mode: str, model: str) -> tuple[str, str]:
    presets = ANTHROPIC_MODE_PRESETS if provider_for_model(model) == "anthropic" else MODE_PRESETS
    return presets[mode]


LANGUAGES = ("auto", "ru", "uk", "en")

DEFAULT_CONFIG: dict = {
    "ui_language": "en",
    "mode": "quick",
    "model": "gpt_mini",
    "effort": "low",
    "audio_source": "system",
    "auto_answer": False,
    "hide_from_capture": True,
    "speech_language": "auto",
    "answer_language": "auto",
    "context": "",
    "glossary": "",
    "whisper_model": "large-v3-turbo",
    "whisper_model_cpu": "small",
    "whisper_device": "auto",
    "vad_threshold": 0.5,
    "end_silence": 0.6,
    "max_transcript_chars": 24000,
    "max_history_tokens": 40000,
    "auto_clear_minutes": 120,
    "screenshot_max_side": 1920,
    "window_alpha": 0.97,
    "font_scale": 1.0,
    "always_on_top": True,
    "budget_usd": 0.0,
    "spent_usd": 0.0,
    "spent_month": "",
    "heard_height": 0,
    "composer_height": 0,
    "close_to_tray": True,
    "mic_device": "",
    "speaker_device": "",
    "profile": "",
    "summary_minutes": 5,
    "geometry": None,
    "compact_mode": False,
    "compact_geometry": None,
    "saved_area": None,
    "hotkeys": {
        "answer": "ctrl+alt+a",
        "screenshot": "ctrl+alt+s",
        "area": "ctrl+alt+r",
        "listen": "ctrl+alt+l",
        "toggle_window": "ctrl+alt+h",
        "summary": "ctrl+alt+m",
        "voice": "ctrl+alt+v",
        "compact": "ctrl+alt+c",
        "choose_area": "ctrl+alt+shift+r",
    },
}

_CHOICES = {
    "ui_language": ("en", "ru"),
    "mode": tuple(MODE_PRESETS),
    "model": tuple(MODEL_PROFILES),
    "effort": ("low", "medium", "high"),
    "audio_source": ("system", "mic", "both"),
    "speech_language": LANGUAGES,
    "answer_language": LANGUAGES,
    "whisper_device": ("auto", "cuda", "cpu"),
}
_RANGES = {
    "vad_threshold": (0.1, 0.95),
    "end_silence": (0.3, 2.0),
    "max_transcript_chars": (2000, 200000),
    "max_history_tokens": (8000, 500000),
    "auto_clear_minutes": (0, 1440),
    "screenshot_max_side": (640, 2576),
    "window_alpha": (0.3, 1.0),
    "font_scale": (0.8, 1.6),
    "heard_height": (0, 4000),
    "composer_height": (0, 4000),
    "budget_usd": (0.0, 100000.0),
    "spent_usd": (0.0, 1e9),
    "summary_minutes": (1, 180),
}
_BOOLS = ("auto_answer", "hide_from_capture", "close_to_tray", "always_on_top", "compact_mode")
_STRINGS = (
    "context",
    "glossary",
    "whisper_model",
    "whisper_model_cpu",
    "mic_device",
    "speaker_device",
    "profile",
    "spent_month",
)


def validate(cfg: dict) -> dict:
    """Replace values that would break the app with defaults."""
    for key, allowed in _CHOICES.items():
        if cfg.get(key) not in allowed:
            log.warning("config: %s=%r is invalid, using %r", key, cfg.get(key), DEFAULT_CONFIG[key])
            cfg[key] = DEFAULT_CONFIG[key]
    for key, (lo, hi) in _RANGES.items():
        try:
            value = float(cfg.get(key, DEFAULT_CONFIG[key]))
        except (TypeError, ValueError):
            log.warning("config: %s=%r is not a number", key, cfg.get(key))
            value = DEFAULT_CONFIG[key]
        value = min(hi, max(lo, value))
        cfg[key] = int(value) if isinstance(DEFAULT_CONFIG[key], int) else value
    for key in _BOOLS:
        cfg[key] = bool(cfg.get(key, DEFAULT_CONFIG[key]))
    for key in _STRINGS:
        if not isinstance(cfg.get(key), str):
            cfg[key] = DEFAULT_CONFIG[key]
    if not isinstance(cfg.get("geometry"), (str, type(None))):
        cfg["geometry"] = None
    if not isinstance(cfg.get("compact_geometry"), (str, type(None))):
        cfg["compact_geometry"] = None
    cfg["saved_area"] = validate_region(cfg.get("saved_area"))
    hotkeys = cfg.get("hotkeys") if isinstance(cfg.get("hotkeys"), dict) else {}
    cfg["hotkeys"] = {name: str(hotkeys.get(name) or combo) for name, combo in DEFAULT_CONFIG["hotkeys"].items()}
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(user, dict):
                raise ValueError("config.json must contain an object")
            for key, value in user.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
        except Exception:
            log.exception("config.json read failed, using defaults")
    return validate(cfg)


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        log.exception("config.json write failed")
