"""Colors, fonts and the application stylesheet: dark, with a warm honey accent."""

from __future__ import annotations

BG, PANEL, CARD, BORDER = "#17181b", "#1f2024", "#26282d", "#33363c"
FG, MUTED, DIM = "#ececec", "#9aa0a6", "#6b7075"
ACCENT, ACCENT_HOVER, ON_ACCENT = "#f0a04b", "#f5b56a", "#1a1205"
USER_C, OK_C, ERR_C, WARN_C = "#8ab4f8", "#81c995", "#f28b82", "#f6c177"
CODE_BG, CODE_FG = "#111214", "#e6db74"
SPEAKER_COLORS = {"system": WARN_C, "mic": USER_C}
FONT_FAMILY, MONO_FAMILY = "Segoe UI", "Consolas"
BASE_PT = 10.0

COLORS = {
    "bg": BG,
    "panel": PANEL,
    "card": CARD,
    "border": BORDER,
    "fg": FG,
    "muted": MUTED,
    "accent": ACCENT,
    "code_bg": CODE_BG,
    "code_fg": CODE_FG,
    "mono": MONO_FAMILY,
}


def pt(scale: float, size: float = BASE_PT) -> str:
    return f"{size * scale:.1f}pt"


def stylesheet(scale: float = 1.0) -> str:
    base, small, tiny = pt(scale), pt(scale, 9), pt(scale, 8)
    return f"""
QWidget {{ background: {BG}; color: {FG}; font-family: "{FONT_FAMILY}"; font-size: {base}; }}
QToolTip {{ background: {CARD}; color: {FG}; border: 1px solid {BORDER}; padding: 5px; font-size: {small}; }}

QPushButton {{ background: {PANEL}; color: {FG}; border: 1px solid transparent; border-radius: 6px;
  padding: 5px 9px; font-size: {small}; }}
QPushButton:hover {{ background: {CARD}; border-color: {BORDER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {DIM}; }}
QPushButton[accent="true"] {{ background: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; }}
QPushButton[accent="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[chip="true"] {{ background: transparent; color: {MUTED}; border-radius: 10px; padding: 2px 9px; }}
QPushButton[chip="true"]:hover {{ color: {FG}; background: {PANEL}; }}
QPushButton[chip="true"]:checked {{ background: {CARD}; color: {ACCENT}; }}
QPushButton[link="true"] {{ background: transparent; color: {MUTED}; padding: 1px 4px; border: none; }}
QPushButton[link="true"]:hover {{ color: {ACCENT}; background: transparent; }}
QPushButton[icon="true"] {{ background: transparent; color: {MUTED}; padding: 3px 7px; font-size: {base}; }}
QPushButton[icon="true"]:hover {{ color: {FG}; background: {PANEL}; }}

QComboBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px;
  font-size: {small}; }}
QComboBox:hover {{ border-color: {DIM}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; border-left: 4px solid transparent;
  border-right: 4px solid transparent; border-top: 5px solid {MUTED}; margin-right: 6px; }}
QComboBox QAbstractItemView {{ background: {CARD}; color: {FG}; border: 1px solid {BORDER};
  selection-background-color: {ACCENT}; selection-color: {ON_ACCENT}; outline: none; }}

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox {{ background: {PANEL};
  border: 1px solid transparent;
  border-radius: 8px; padding: 5px; selection-background-color: {ACCENT}; selection-color: {ON_ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{ border-color: {BORDER}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}

QCheckBox, QRadioButton {{ spacing: 7px; font-size: {small}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
  background: {PANEL}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {DIM}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 3px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -5px 0; background: {FG}; border-radius: 7px; }}

QListWidget {{ background: {PANEL}; border: none; border-radius: 8px; outline: none; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {CARD}; color: {ACCENT}; }}
QListWidget::item:hover {{ background: {CARD}; }}

QProgressBar {{ background: {PANEL}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QFrame[panel="true"] {{ background: {PANEL}; border-radius: 8px; }}
QFrame[separator="true"] {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QLabel[muted="true"] {{ color: {MUTED}; font-size: {small}; }}
QLabel[tiny="true"] {{ color: {DIM}; font-size: {tiny}; }}
QLabel[heading="true"] {{ font-size: {pt(scale, 12)}; font-weight: 600; }}
QDialog {{ background: {BG}; }}
QFrame[titlebar="true"] {{ background: {BG}; }}
QFrame[banner="true"] {{ background: #3a2426; border: 1px solid {ERR_C}; border-radius: 6px; }}
QFrame[banner="true"] QLabel {{ color: {ERR_C}; font-size: {small}; background: transparent; }}
QFrame[banner="true"] QPushButton {{ color: {ERR_C}; background: transparent; }}
QPushButton[icon="true"][active="true"] {{ color: {ACCENT}; }}
QPushButton[close="true"]:hover {{ background: {ERR_C}; color: {ON_ACCENT}; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {BORDER}; border-radius: 3px; }}
QSizeGrip {{ width: 12px; height: 12px; background: transparent; }}
"""
