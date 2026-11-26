"""Compatibility exports for the original Anthropic key helper."""

from overlay.ai.keys import (  # noqa: F401
    ALLOWED_ENV,
    KEY_NAME,
    KEY_NAMES,
    KEY_PATHS,
    apply_env,
    blank_env_value,
    dpapi,
    load_api_key,
    read_env,
    save_api_key,
)
