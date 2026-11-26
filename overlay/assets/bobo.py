"""Bobo the bear: pixel sprites written as text and rendered without smoothing at integer scales.

One character per pixel. The bust is built from a mirrored half, ears and face parts are patches,
so every state is a small recipe instead of a hand-copied grid. No Qt here: the sprites are tested
and turned into files (`python -m overlay.assets.bobo --preview DIR`, `--ico FILE`) without a display.

    python -m overlay.assets.bobo --preview out/    contact sheet of every state at x6
    python -m overlay.assets.bobo --ico bobo.ico    the Windows icon (16, 32, 48, 256 px)
"""

from __future__ import annotations

import argparse
import pathlib

from PIL import Image

SIZE = 32
ICON_SIZE = 16

PALETTE: dict[str, tuple[int, int, int, int] | None] = {
    ".": None,
    "D": (0x5A, 0x3D, 0x24, 255),  # dark fur, outline
    "F": (0x8C, 0x62, 0x39, 255),  # fur
    "L": (0xA6, 0x7C, 0x4E, 255),  # fur highlight
    "E": (0xC4, 0x8A, 0x5A, 255),  # inner ear
    "M": (0xE9, 0xCF, 0xA6, 255),  # muzzle
    "K": (0x1C, 0x17, 0x14, 255),  # eyes, nose
    "W": (0xFF, 0xFF, 0xFF, 255),  # glints, z's
    "B": (0xE8, 0x95, 0x5E, 255),  # blush
    "S": (0xF0, 0xA0, 0x4B, 255),  # scarf, sound, thought dots (the accent color)
    "R": (0xF2, 0x8B, 0x82, 255),  # the error mark, the on-air lamp
    "Q": (0x7A, 0x33, 0x30, 255),  # the on-air lamp between blinks
    "A": (0x9E, 0xD0, 0xF5, 255),  # a drop of sweat
}

Rows = list[str]
Patch = tuple[int, int, Rows]  # row, column, pixels ('.' keeps what is below)

# Left half of the bust without ears or eyes; the right half is its mirror.
_HALF: Rows = [
    "................",
    "................",
    "................",
    "................",
    "........DDDDDDDD",
    "......DDFFFFFFFF",
    "....DDFFFFFFFFFF",
    "...DFFFFFFFFFFFF",
    "..DFFFFFFFFFFFFF",
    ".DFFFFFFFFFFFFFF",
    ".DFFFFFFFFFFFFFF",
    ".DFFFFFFFFFFFFFF",
    ".DFFFFFFFFFFFFFF",
    ".DFFFFFFFFFFFFFF",
    ".DFFFFFFFFFLMMMM",
    ".DFFFFFFFFLMMMMM",
    ".DFFFFFFFFLMMMMM",
    ".DFFFFFFFFLMMMKK",
    ".DFFFFFFFFLMMMKK",
    "..DFFFFFFFLMMMMM",
    "..DFFFFFFFFLMMMM",
    "...DFFFFFFFLMMMM",
    "....DFFFFFFFLMMM",
    ".....DDFFFFFFFFF",
    ".......DDFFFFFFF",
    ".........DDDDDDD",
    "........DSSSSSSS",
    ".......DSSSSSSSS",
    "......DFFSSSSSSS",
    ".....DFFFFFFSSSS",
    "....DFFFFFFFFFFF",
    "....DFFFFFFFFFFF",
]

_EAR_LEFT: Rows = [
    "..DDDD..",
    ".DFFFFD.",
    "DFEEEFFD",
    "DFEEEEFD",
    "DFEEEEFD",
    "DFFEEEFD",
    "DFFFFFFD",
    ".DFFFFD.",
]
_EAR_RIGHT: Rows = [row[::-1] for row in _EAR_LEFT]
_EAR_COL = {"left": 2, "right": 22}
_EAR_ROW = {"normal": 2, "up": 0, "down": 4}

_EYES: dict[str, tuple[Rows, int, int, int]] = {
    # name: (pixels, row, left column, right column)
    "open": (["WK", "KK"], 11, 7, 23),
    "closed": (["..", "KK"], 11, 7, 23),
    "up": (["WK", "KK"], 10, 8, 24),
    "happy": ([".K.", "K.K"], 11, 6, 23),
    "x": (["K.K", ".K.", "K.K"], 10, 6, 23),
    "side": (["WK", "KK"], 11, 9, 25),
    "wide": (["WK", "KK", "KK"], 10, 7, 23),
}

MOUTH_OPEN: Patch = (20, 14, ["KKKK", ".KK."])
MOUTH_SMILE: Patch = (20, 12, ["K......K", ".KKKKKK."])
BLUSH_LEFT: Patch = (14, 4, ["BB", "BB"])
BLUSH_RIGHT: Patch = (14, 26, ["BB", "BB"])
Z_SMALL: Patch = (4, 27, ["WWW", ".W.", "WWW"])
Z_BIG: Patch = (0, 27, ["WWWWW", "...W.", "..W..", ".W...", "WWWWW"])
ARC_NEAR: Patch = (11, 28, [".S", "S.", "S.", "S.", "S.", "S.", "S.", ".S"])
ARC_FAR: Patch = (9, 30, [".S", "S.", "S.", "S.", "S.", "S.", "S.", "S.", "S.", "S.", "S.", ".S"])
DOT_1: Patch = (5, 26, ["SS", "SS"])
DOT_2: Patch = (2, 28, ["SS", "SS"])
DOT_3: Patch = (0, 30, ["SS", "SS"])
EXCLAMATION: Patch = (1, 28, ["RR", "RR", "RR", "RR", "..", "RR"])
LAMP_ON: Patch = (0, 14, [".RR.", "RWRR", "RRRR", ".RR."])
LAMP_OFF: Patch = (0, 14, [".QQ.", "QQQQ", "QQQQ", ".QQ."])
SWEAT: Patch = (7, 3, [".A", ".A", "AA"])
MOUTH_O: Patch = (20, 15, ["KK", "KK"])
PAW_UP: Patch = (15, 27, [".DDD.", "DFFFD", "DFLFD", "DFFFD", ".DDD."])
PAW_DOWN: Patch = (19, 27, [".DDD.", "DFFFD", "DFLFD", "DFFFD", ".DDD."])


def apply(rows: Rows, patch: Patch) -> Rows:
    top, left, pixels = patch
    out = [list(row) for row in rows]
    for dy, line in enumerate(pixels):
        y = top + dy
        if not 0 <= y < len(out):
            continue
        for dx, ch in enumerate(line):
            x = left + dx
            if ch != "." and 0 <= x < len(out[y]):
                out[y][x] = ch
    return ["".join(row) for row in out]


def shift(rows: Rows, dx: int) -> Rows:
    width = len(rows[0])
    if dx >= 0:
        return [("." * dx + row)[:width] for row in rows]
    return [(row[-dx:] + "." * -dx)[:width] for row in rows]


def bust(ears: str = "normal", eyes: str = "open", extras: tuple[Patch, ...] = ()) -> Rows:
    rows = [half + half[::-1] for half in _HALF]
    rows = apply(rows, (_EAR_ROW[ears], _EAR_COL["left"], _EAR_LEFT))
    rows = apply(rows, (_EAR_ROW[ears], _EAR_COL["right"], _EAR_RIGHT))
    pixels, row, left, right = _EYES[eyes]
    rows = apply(rows, (row, left, pixels))
    rows = apply(rows, (row, right, pixels))
    for patch in extras:
        rows = apply(rows, patch)
    return rows


Frame = tuple[Rows, int]  # pixels, milliseconds


def _frames() -> dict[str, list[Frame]]:
    listening = [
        (bust("up", "open", (ARC_NEAR,)), 350),
        (bust("up", "open", (ARC_NEAR, ARC_FAR)), 350),
        (bust("up", "open", (ARC_FAR,)), 350),
    ]
    return {
        "sleep": [(bust("normal", "closed", (Z_SMALL,)), 900), (bust("normal", "closed", (Z_BIG,)), 900)],
        "idle": [(bust("normal", "open"), 3800), (bust("normal", "closed"), 140)],
        "listening": listening,
        "hearing": [(rows if i % 2 else shift(rows, 1), 180) for i, (rows, _) in enumerate(listening)],
        "thinking": [
            (bust("normal", "up", (DOT_1,)), 300),
            (bust("normal", "up", (DOT_1, DOT_2)), 300),
            (bust("normal", "up", (DOT_1, DOT_2, DOT_3)), 300),
        ],
        "talking": [(bust("normal", "open", (MOUTH_OPEN,)), 200), (bust("normal", "open"), 200)],
        "happy": [(bust("up", "happy", (MOUTH_SMILE, BLUSH_LEFT, BLUSH_RIGHT)), 1500)],
        "error": [(bust("down", "x", (EXCLAMATION,)), 1000)],
        "curious": [(bust("up", "open"), 1000)],
        "visible": [
            (bust("up", "wide", (BLUSH_LEFT, BLUSH_RIGHT, SWEAT, MOUTH_O, LAMP_ON)), 550),
            (bust("up", "wide", (BLUSH_LEFT, BLUSH_RIGHT, SWEAT, MOUTH_O, LAMP_OFF)), 550),
        ],
        "wave": [
            (bust("up", "happy", (MOUTH_SMILE, PAW_UP)), 280),
            (bust("up", "happy", (MOUTH_SMILE, PAW_DOWN)), 280),
        ],
    }


STATES = _frames()


def frames(state: str) -> list[Frame]:
    return STATES.get(state) or STATES["sleep"]


# --- the 16 px face for the tray and the window icon ---

_ICON_BASE: Rows = [
    "................",
    "..DDD......DDD..",
    ".DFEFD....DFEFD.",
    ".DFEEDDDDDDEEFD.",
    ".DFFFFFFFFFFFFD.",
    "..DFFFFFFFFFFD..",
    "..DFFFFFFFFFFD..",
    "..DFFFFMMMFFFD..",
    "..DFFFMMMMMFFD..",
    "..DFFFMMKKMFFD..",
    "..DFFFMMMMMFFD..",
    "...DFFFMMMFFFD..",
    "....DFFFFFFFD...",
    ".....DSSSSSD....",
    "................",
    "................",
]
_ICON_EYES = {
    "open": ((6, 5, ["K"]), (6, 10, ["K"])),
    "closed": ((6, 4, ["KK"]), (6, 10, ["KK"])),
    "happy": ((5, 4, [".K.", "K.K"]), (5, 9, [".K.", "K.K"])),
    "x": ((5, 4, ["K.K", ".K.", "K.K"]), (5, 9, ["K.K", ".K.", "K.K"])),
}
_ICON_STATES: dict[str, tuple[str, tuple[Patch, ...]]] = {
    "sleep": ("closed", ((1, 13, ["WW", ".W", "WW"]),)),
    "idle": ("open", ()),
    "listening": ("open", ((5, 14, [".S", "S.", "S.", "S.", ".S"]),)),
    "busy": ("open", ((0, 12, ["SS", "SS"]),)),
    "happy": ("happy", ()),
    "error": ("x", ((0, 13, ["RR", "RR", "RR", "..", "RR"]),)),
    "visible": ("open", ((0, 12, [".RR.", "RWRR", ".RR."]),)),
}
ICON_FOR_STATE = {
    "sleep": "sleep",
    "idle": "idle",
    "listening": "listening",
    "hearing": "listening",
    "thinking": "busy",
    "talking": "busy",
    "happy": "happy",
    "error": "error",
    "curious": "idle",
    "wave": "happy",
    "visible": "visible",
}


def icon_rows(state: str) -> Rows:
    eyes, extras = _ICON_STATES.get(ICON_FOR_STATE.get(state, state), _ICON_STATES["idle"])
    rows = _ICON_BASE
    for patch in _ICON_EYES[eyes] + extras:
        rows = apply(rows, patch)
    return rows


# --- rendering ---


def render(rows: Rows, scale: int = 1) -> Image.Image:
    """RGBA image, nearest-neighbour scaled: pixels stay square."""
    height, width = len(rows), len(rows[0])
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = image.load()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            color = PALETTE[ch]
            if color is not None:
                pixels[x, y] = color
    if scale > 1:
        image = image.resize((width * scale, height * scale), Image.Resampling.NEAREST)
    return image


def contact_sheet(scale: int = 6, gap: int = 8) -> Image.Image:
    """Every state, one row per state, for a visual check."""
    columns = max(len(f) for f in STATES.values())
    cell = SIZE * scale
    sheet = Image.new("RGBA", (columns * (cell + gap) + gap, len(STATES) * (cell + gap) + gap), (0x17, 0x18, 0x1B, 255))
    for r, state_frames in enumerate(STATES.values()):
        for c, (rows, _ms) in enumerate(state_frames):
            sheet.alpha_composite(render(rows, scale), (gap + c * (cell + gap), gap + r * (cell + gap)))
    icons = Image.new(
        "RGBA", (len(_ICON_STATES) * (ICON_SIZE * 4 + gap) + gap, ICON_SIZE * 4 + 2 * gap), (0x17, 0x18, 0x1B, 255)
    )
    for c, state in enumerate(_ICON_STATES):
        icons.alpha_composite(render(icon_rows(state), 4), (gap + c * (ICON_SIZE * 4 + gap), gap))
    out = Image.new("RGBA", (max(sheet.width, icons.width), sheet.height + icons.height), (0x17, 0x18, 0x1B, 255))
    out.alpha_composite(sheet, (0, 0))
    out.alpha_composite(icons, (0, sheet.height))
    return out


def build_ico(path: pathlib.Path) -> None:
    """256 px from the bust, 48, 32 and 16 px from the small face: each an exact integer scale.
    Pillow keeps only sizes up to the first image, so the largest one goes first."""
    face = icon_rows("idle")
    images = [render(bust(), 8), render(face, 3), render(face, 2), render(face, 1)]
    images[0].save(path, format="ICO", sizes=[(im.width, im.height) for im in images], append_images=images[1:])


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Bobo.")
    parser.add_argument("--preview", metavar="DIR", help="write bobo_sheet.png with every state")
    parser.add_argument("--ico", metavar="FILE", help="write the Windows icon")
    args = parser.parse_args()
    if args.preview:
        out = pathlib.Path(args.preview)
        out.mkdir(parents=True, exist_ok=True)
        contact_sheet().save(out / "bobo_sheet.png")
        print(out / "bobo_sheet.png")
    if args.ico:
        build_ico(pathlib.Path(args.ico))
        print(args.ico)


if __name__ == "__main__":
    main()
