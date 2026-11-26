"""The first start without a key: paste it, check it, pick what to listen to."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from overlay.config import MODEL_PROFILES, provider_for_model
from overlay.core.labels import sources
from overlay.gui.mascot import BoboWidget
from overlay.gui.theme import ERR_C, OK_C
from overlay.i18n import t
from overlay.win.hotkeys import pretty_hotkey


class FirstRunDialog(QDialog):
    checked = Signal(str, bool, int)

    def __init__(self, cfg: dict):
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.cfg = cfg
        self.key: str | None = None
        self.provider = provider_for_model(cfg["model"])
        self._generation = 0
        self.setWindowTitle(t("fr_title"))
        self.setMinimumWidth(480)
        self.resize(520, 640)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(BoboWidget(64, "happy"))
        title = QLabel(t("fr_title"))
        title.setProperty("heading", True)
        head.addWidget(title, 1)
        layout.addLayout(head)

        layout.addWidget(self._label("provider"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI / ChatGPT", "openai")
        self.provider_combo.addItem("Anthropic / Claude", "anthropic")
        self.provider_combo.setCurrentIndex(0 if self.provider == "openai" else 1)
        layout.addWidget(self.provider_combo)

        self.intro = QLabel()
        self.intro.setProperty("muted", True)
        self.intro.setWordWrap(True)
        self.intro.setOpenExternalLinks(True)
        layout.addWidget(self.intro)

        layout.addWidget(self._label("fr_key"))
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.textChanged.connect(self._on_key_changed)
        key_row.addWidget(self.key_edit, 1)
        self.check_btn = QPushButton(t("fr_check"))
        self.check_btn.clicked.connect(self._check)
        key_row.addWidget(self.check_btn)
        layout.addLayout(key_row)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

        layout.addWidget(self._label("fr_source"))
        self.source = QComboBox()
        self.source_keys = list(sources())
        for label in sources().values():
            self.source.addItem(label)
        self.source.setCurrentIndex(self.source_keys.index(cfg["audio_source"]))
        layout.addWidget(self.source)

        hk = {name: pretty_hotkey(combo) for name, combo in cfg["hotkeys"].items()}
        hotkeys = QLabel(
            t("fr_hotkeys")
            + "\n"
            + "\n".join(
                f"{hk[name]} — {t(f'hotkey_{name}')}"
                for name in ("listen", "voice", "screenshot", "area", "compact", "toggle_window")
            )
        )
        hotkeys.setProperty("muted", True)
        hotkeys.setMinimumHeight(hotkeys.sizeHint().height())
        layout.addWidget(hotkeys)

        privacy = QLabel(t("fr_privacy"))
        privacy.setProperty("tiny", True)
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        buttons = QHBoxLayout()
        skip = QPushButton(t("fr_skip"))
        skip.setProperty("link", True)
        skip.clicked.connect(self.reject)
        buttons.addWidget(skip)
        buttons.addStretch(1)
        self.save_btn = QPushButton(t("fr_save"))
        self.save_btn.setProperty("accent", True)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)
        self.checked.connect(self._on_checked)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._on_provider_changed()

    def _on_provider_changed(self) -> None:
        self.provider = self.provider_combo.currentData()
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("sk-…" if self.provider == "openai" else "sk-ant-…")
        self.intro.setText(t(f"fr_intro_{self.provider}"))
        self._on_key_changed("")

    def _label(self, key: str) -> QLabel:
        label = QLabel(t(key))
        label.setProperty("muted", True)
        return label

    def _on_key_changed(self, text: str) -> None:
        self._generation += 1
        self.check_btn.setEnabled(True)
        self.save_btn.setEnabled(bool(text.strip()))
        self.result.setText("")

    def _check(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.result.setText(t("fr_empty"))
            return
        self.check_btn.setEnabled(False)
        self.result.setStyleSheet("")
        self.result.setText(t("fr_checking"))
        provider, generation = self.provider, self._generation
        model_key = self.cfg["model"]
        if provider_for_model(model_key) != provider:
            model_key = "gpt_mini" if provider == "openai" else "haiku"
        model = MODEL_PROFILES[model_key]["model"]

        def work():
            try:
                if provider == "openai":
                    import openai

                    from overlay.ai.openai_client import API_BASE_URL

                    with openai.OpenAI(api_key=key, base_url=API_BASE_URL, timeout=20.0, max_retries=0) as client:
                        client.models.retrieve(model)
                else:
                    import anthropic

                    from overlay.claude.client import API_BASE_URL

                    with anthropic.Anthropic(api_key=key, base_url=API_BASE_URL, timeout=20.0, max_retries=0) as client:
                        client.messages.count_tokens(model=model, messages=[{"role": "user", "content": "hi"}])
                self.checked.emit(t("fr_ok"), True, generation)
            except Exception as e:
                if provider == "openai":
                    from overlay.ai.openai_client import describe_error
                else:
                    from overlay.claude.client import describe_error

                self.checked.emit(describe_error(e, model), False, generation)

        threading.Thread(target=work, name="key-check", daemon=True).start()

    def _on_checked(self, text: str, ok: bool, generation: int) -> None:
        if generation != self._generation:
            return
        self.check_btn.setEnabled(True)
        self.result.setStyleSheet(f"color: {OK_C if ok else ERR_C};")
        self.result.setText(text)

    def _save(self) -> None:
        self.key = self.key_edit.text().strip()
        if not self.key:
            return
        if self.provider == "openai" and self.key.startswith("sk-ant-"):
            self.result.setText(t("err_wrong_provider"))
            self.key = None
            return
        if provider_for_model(self.cfg["model"]) != self.provider:
            self.cfg["model"] = "gpt_mini" if self.provider == "openai" else "sonnet"
        self.cfg["audio_source"] = self.source_keys[self.source.currentIndex()]
        self.accept()
