"""Validate saved capture coordinates without ever falling back to a larger capture."""

from __future__ import annotations


def validate_region(value) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    names = ("left", "top", "width", "height")
    if any(type(value.get(name)) is not int for name in names):
        return None
    region = {name: value[name] for name in names}
    if not (8 <= region["width"] <= 32768 and 8 <= region["height"] <= 32768):
        return None
    if abs(region["left"]) > 100000 or abs(region["top"]) > 100000:
        return None
    return region


def region_on_screen(region: dict, monitors: list[dict]) -> bool:
    return any(
        m["left"] <= region["left"]
        and m["top"] <= region["top"]
        and region["left"] + region["width"] <= m["left"] + m["width"]
        and region["top"] + region["height"] <= m["top"] + m["height"]
        for m in monitors
    )
