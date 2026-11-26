"""LaTeX to plain text, for what matplotlib's mathtext cannot typeset: matrices, systems, unknown commands."""

from __future__ import annotations

import re

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}  # fmt: skip
_COMMANDS = _GREEK | {
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "~",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "Leftrightarrow": "⇔",
    "iff": "⇔",
    "implies": "⇒",
    "infty": "∞",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "cup": "∪",
    "cap": "∩",
    "forall": "∀",
    "exists": "∃",
    "emptyset": "∅",
    "partial": "∂",
    "nabla": "∇",
    "sum": "Σ",
    "prod": "Π",
    "int": "∫",
    "ldots": "…",
    "cdots": "⋯",
    "dots": "…",
    "quad": "  ",
    "qquad": "    ",
    "lim": "lim",
    "log": "log",
    "ln": "ln",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "det": "det",
    "max": "max",
    "min": "min",
    "left": "",
    "right": "",
    "displaystyle": "",
    "big": "",
    "Big": "",
}
_SUPERSCRIPT = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPT = str.maketrans("0123456789+-=()aeoxijnmk", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₙₘₖ")
_SUP_CHARS, _SUB_CHARS = set("0123456789+-=()ni"), set("0123456789+-=()aeoxijnmk")

_MATRIX_RE = re.compile(r"\\begin\{([pbvBV]?matrix|array)\}(?:\{[^}]*\})?(.*?)\\end\{\1\}", re.S)
_BRACKETS = {"pmatrix": "()", "bmatrix": "[]", "Bmatrix": "{}", "vmatrix": "||", "Vmatrix": "‖‖"}
DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
INLINE_MATH_RE = re.compile(r"\\\((.+?)\\\)|\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)")
CODE_SPAN_RE = re.compile(r"`[^`\n]+`")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _script(text: str, sup: bool) -> str:
    chars, table, mark = (_SUP_CHARS, _SUPERSCRIPT, "^") if sup else (_SUB_CHARS, _SUBSCRIPT, "_")
    text = text.strip()
    if text and set(text) - {",", " "} <= chars:
        return text.translate(table)
    return f"{mark}({text})" if len(text) > 1 else f"{mark}{text}"


def _wrap(term: str) -> str:
    term = term.strip()
    return term if re.fullmatch(r"[\w.]+|\(.*\)", term) else f"({term})"


def latex_inline(tex: str) -> str:
    """One formula without matrices -> Unicode text."""
    for _ in range(8):
        before = tex
        tex = re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", lambda m: f"{_wrap(m[1])}/{_wrap(m[2])}", tex)
        tex = re.sub(r"\\sqrt\[([^\]]*)\]\{([^{}]*)\}", lambda m: f"{_script(m[1], True)}√{_wrap(m[2])}", tex)
        tex = re.sub(r"\\sqrt\{([^{}]*)\}", lambda m: f"√{_wrap(m[1])}", tex)
        tex = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname|vec|bar|hat|overline)\{([^{}]*)\}", r"\1", tex)
        tex = re.sub(r"\^\{([^{}]*)\}", lambda m: _script(m[1], True), tex)
        tex = re.sub(r"_\{([^{}]*)\}", lambda m: _script(m[1], False), tex)
        if tex == before:
            break
    tex = re.sub(r"\^(\w)", lambda m: _script(m[1], True), tex)
    tex = re.sub(r"_(\w)", lambda m: _script(m[1], False), tex)
    tex = re.sub(r"\\([A-Za-z]+)", lambda m: _COMMANDS.get(m[1], m[1]), tex)
    tex = re.sub(r"\\[,;:! ]", " ", tex).replace("\\{", "{").replace("\\}", "}")
    tex = tex.replace("{", "").replace("}", "")
    return re.sub(r"[ \t]{2,}", " ", tex).strip()


def align_rows(rows: list[list[str]], left: str = "", right: str = "", sep: str = "  ") -> list[str]:
    widths = [max(len(row[i]) if i < len(row) else 0 for row in rows) for i in range(max(map(len, rows)))]
    lines = []
    for row in rows:
        cells = [(row[i] if i < len(row) else "").rjust(widths[i]) for i in range(len(widths))]
        lines.append(f"{left} {sep.join(cells)} {right}".strip() if left or right else sep.join(cells).rstrip())
    return lines


def _matrix(match: re.Match) -> str:
    left, right = _BRACKETS.get(match[1], "  ")
    rows = [r for r in re.split(r"\\\\", match[2]) if r.strip() and not r.strip().startswith(r"\hline")]
    cells = [[latex_inline(c) for c in row.split("&")] for row in rows]
    return "\n".join(align_rows(cells, left.strip(), right.strip())) if cells else ""


def has_matrix(tex: str) -> bool:
    return bool(_MATRIX_RE.search(tex))


def math_block(tex: str) -> str:
    """A display formula; matrices go on their own aligned lines."""
    parts, pos = [], 0
    for m in _MATRIX_RE.finditer(tex):
        prefix = latex_inline(tex[pos : m.start()])
        if prefix:
            parts.append(prefix)
        parts.append(_matrix(m))
        pos = m.end()
    tail = latex_inline(tex[pos:])
    if tail:
        parts.append(tail)
    return "\n".join(parts)


def is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line))
