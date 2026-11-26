"""Option labels for the window, in the interface language."""

from __future__ import annotations

from overlay.i18n import t


def modes() -> dict[str, str]:
    return {key: t(f"mode_{key}") for key in ("quick", "interview", "explain", "solve")}


def modes_short() -> dict[str, str]:
    return {key: t(f"mode_short_{key}") for key in ("quick", "interview", "explain", "solve")}


def sources() -> dict[str, str]:
    return {"system": t("source_system"), "mic": t("source_mic"), "both": t("source_both")}


def efforts() -> dict[str, str]:
    return {"low": t("effort_low"), "medium": t("effort_medium"), "high": t("effort_high")}


def languages() -> dict[str, str]:
    return {"auto": t("lang_auto"), "ru": "RU", "uk": "UK", "en": "EN"}
