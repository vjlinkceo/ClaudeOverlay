"""API key: .env -> Windows DPAPI encryption -> api_key.dpapi.

Lookup order: the selected provider API key from the system environment, then .env (encrypted and blanked),
then api_key.dpapi.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import re
from pathlib import Path

from overlay.config import APP_DIR, ENC_KEY_PATH, ENV_PATH

log = logging.getLogger(__name__)

KEY_NAME = "ANTHROPIC_API_KEY"
KEY_NAMES = {"anthropic": KEY_NAME, "openai": "OPENAI_API_KEY"}
KEY_PATHS = {"anthropic": ENC_KEY_PATH, "openai": APP_DIR / "openai_api_key.dpapi"}
# A planted .env with HTTPS_PROXY / SSL_CERT_FILE / HF_ENDPOINT could route traffic (and the key)
# through someone else's server, so only these names are applied to the environment.
ALLOWED_ENV = {"HF_TOKEN"}
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else raw[1:]
    if raw.startswith("#"):
        return ""
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        log.exception(".env read failed")
        return values
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m:
            values[m.group(1)] = _parse_value(m.group(2))
    return values


def blank_env_value(path: Path, name: str, note: str = "") -> None:
    """Clear one variable's value in .env, keeping every other line intact."""
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m and m.group(1) == name:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[i] = f"{name}={note}{ending}"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.replace(path)


def apply_env(values: dict[str, str]) -> None:
    for name, value in values.items():
        if name in ALLOWED_ENV and value:
            os.environ.setdefault(name, value)
        elif name not in KEY_NAMES.values() and value:
            log.warning(".env: variable %s is not allowed and was ignored", name)


def _is_placeholder(value: str) -> bool:
    return (
        not value or "..." in value or value.lower() in ("your-api-key", "your_api_key", "sk-ant-", "sk-", "sk-proj-")
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi(data: bytes, encrypt: bool) -> bytes:
    """Windows DPAPI: only this Windows user on this computer can decrypt."""
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        wt.LPCWSTR,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        ctypes.c_void_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        blob_pointer,
    ]
    ui_forbidden = 0x1
    if encrypt:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), "ClaudeOverlay", None, None, None, ui_forbidden, ctypes.byref(blob_out)
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, ui_forbidden, ctypes.byref(blob_out)
        )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(blob_out.pbData)


def load_api_key(env_path: Path = ENV_PATH, enc_path: Path | None = None, *, provider: str = "anthropic") -> str | None:
    name = KEY_NAMES[provider]
    enc_path = enc_path or KEY_PATHS[provider]
    env_values = read_env(env_path)
    apply_env(env_values)

    key = os.environ.get(name, "").strip()
    if key:
        return key

    plain = env_values.get(name, "").strip()
    if not _is_placeholder(plain):
        try:
            enc_path.write_bytes(dpapi(plain.encode("utf-8"), encrypt=True))
            blank_env_value(env_path, name, f"  # encrypted into {enc_path.name}; put a new key here to replace it")
            log.info("API key from .env encrypted into %s, value in .env cleared", enc_path.name)
        except Exception:
            log.exception("API key encryption failed, key stays in .env")
        return plain

    if enc_path.exists():
        try:
            return dpapi(enc_path.read_bytes(), encrypt=False).decode("utf-8")
        except Exception:
            log.exception("%s decrypt failed", enc_path.name)
    return None


def save_api_key(
    key: str, env_path: Path = ENV_PATH, enc_path: Path | None = None, *, provider: str = "anthropic"
) -> None:
    """A key typed into the window goes straight to DPAPI; a stale value in .env is cleared."""
    name = KEY_NAMES[provider]
    enc_path = enc_path or KEY_PATHS[provider]
    enc_path.write_bytes(dpapi(key.strip().encode("utf-8"), encrypt=True))
    if env_path.exists() and read_env(env_path).get(name):
        blank_env_value(env_path, name, f"  # encrypted into {enc_path.name}; put a new key here to replace it")
    log.info("API key saved to %s", enc_path.name)
