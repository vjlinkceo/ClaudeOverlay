"""Managing profiles: the list on the left, the selected profile's settings, notes and documents on the right."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from overlay.config import MODEL_PROFILES
from overlay.core import profiles
from overlay.core.labels import efforts, languages, modes, sources
from overlay.gui.theme import ERR_C, MUTED
from overlay.i18n import t


class ProfilesDialog(QDialog):
    def __init__(self, cfg: dict, window):
        super().__init__(window, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.cfg = cfg
        self.window_ = window
        self.current: profiles.Profile | None = None
        self._loading = False
        self._dirty_slugs: set[str] = set()
        self.setWindowTitle(t("profiles_title"))
        self.setModal(False)
        self.setMinimumSize(640, 480)
        self.resize(780, 600)
        self.setSizeGripEnabled(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(160)
        self.list.setMaximumWidth(260)
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        new = QPushButton(t("profile_new"))
        new.setProperty("accent", True)
        new.clicked.connect(self._new)
        delete = QPushButton(t("profile_delete"))
        delete.clicked.connect(self._delete)
        buttons.addWidget(new)
        buttons.addWidget(delete)
        left.addLayout(buttons)
        layout.addLayout(left)

        self.form_widget = QWidget()
        right = QVBoxLayout(self.form_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.name = QLineEdit()
        self.name.textChanged.connect(self._field_changed)
        form.addRow(self._label("profile_name"), self.name)
        self.combos: dict[str, tuple[QComboBox, list[str]]] = {}
        label_keys = {"audio_source": "source", "speech_language": "speech", "answer_language": "reply"}
        for key, labels in (
            ("mode", modes()),
            ("model", {k: p["label"] for k, p in MODEL_PROFILES.items()}),
            ("effort", efforts()),
            ("audio_source", sources()),
            ("speech_language", languages()),
            ("answer_language", languages()),
        ):
            combo = QComboBox()
            for value in labels.values():
                combo.addItem(value)
            combo.currentIndexChanged.connect(self._field_changed)
            self.combos[key] = (combo, list(labels))
            form.addRow(self._label(label_keys.get(key, key)), combo)
        self.topic = QLineEdit()
        self.topic.textChanged.connect(self._field_changed)
        form.addRow(self._label("topic"), self.topic)
        self.terms = QPlainTextEdit()
        self.terms.setMinimumHeight(self.terms.fontMetrics().lineSpacing() * 2 + 16)
        self.terms.setMaximumHeight(self.terms.fontMetrics().lineSpacing() * 4 + 16)
        self.terms.textChanged.connect(self._field_changed)
        form.addRow(self._label("terms"), self.terms)
        right.addLayout(form)

        right.addWidget(self._label("profile_notes"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(t("profile_notes_hint"))
        self.notes.setMinimumHeight(self.notes.fontMetrics().lineSpacing() * 3 + 16)
        self.notes.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.notes.textChanged.connect(self._field_changed)
        right.addWidget(self.notes, 2)

        right.addWidget(self._label("profile_docs"))
        self.docs = QListWidget()
        self.docs.setMinimumHeight(70)
        self.docs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.docs.itemChanged.connect(self._doc_toggled)
        right.addWidget(self.docs, 1)
        doc_buttons = QHBoxLayout()
        add = QPushButton(t("profile_add_doc"))
        add.clicked.connect(self._add_docs)
        remove = QPushButton(t("profile_remove_doc"))
        remove.clicked.connect(self._remove_doc)
        doc_buttons.addWidget(add)
        doc_buttons.addWidget(remove)
        doc_buttons.addStretch(1)
        right.addLayout(doc_buttons)
        self.size = QLabel()
        self.size.setProperty("muted", True)
        self.size.setWordWrap(True)
        right.addWidget(self.size)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {ERR_C};")
        right.addWidget(self.message)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        use = QPushButton(t("profile_use"))
        use.setProperty("accent", True)
        use.clicked.connect(self._use)
        bottom.addWidget(use)
        right.addLayout(bottom)
        right_box = QWidget()
        right_col = QVBoxLayout(right_box)
        right_col.setContentsMargins(0, 0, 0, 0)
        self.empty_hint = QLabel(t("profiles_empty"))
        self.empty_hint.setProperty("muted", True)
        self.empty_hint.setWordWrap(True)
        right_col.addWidget(self.empty_hint)
        right_col.addWidget(self.form_widget, 1)
        layout.addWidget(right_box, 1)
        self.reload()

    def _label(self, key: str) -> QLabel:
        label = QLabel(t(key))
        label.setProperty("muted", True)
        return label

    # --- list ---

    def reload(self, select: str | None = None) -> None:
        select = select or self.cfg.get("profile") or ""
        self._loading = True
        self.list.clear()
        for profile in profiles.list_profiles():
            item = QListWidgetItem(profile.name)
            item.setData(Qt.ItemDataRole.UserRole, profile.slug)
            self.list.addItem(item)
            if profile.slug == select:
                self.list.setCurrentItem(item)
        self._loading = False
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        elif not self.list.count():
            self._show(None)
        else:
            self._select(self.list.currentRow())

    def _select(self, row: int) -> None:
        if self._loading:
            return
        item = self.list.item(row)
        self._show(profiles.load(item.data(Qt.ItemDataRole.UserRole)) if item else None)

    def _show(self, profile: profiles.Profile | None) -> None:
        self.current = profile
        self.form_widget.setEnabled(profile is not None)
        self.empty_hint.setVisible(profile is None)
        self._loading = True
        try:
            self.name.setText(profile.name if profile else "")
            for key, (combo, keys) in self.combos.items():
                value = (profile.settings.get(key) if profile else None) or self.cfg.get(key)
                combo.setCurrentIndex(keys.index(value) if value in keys else 0)
            self.topic.setText(profile.settings.get("context", "") if profile else "")
            self.terms.setPlainText(profile.settings.get("glossary", "") if profile else "")
            self.notes.setPlainText(profile.notes if profile else "")
            self.docs.clear()
            for doc in profile.docs if profile else []:
                item = QListWidgetItem(doc.title)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if doc.enabled else Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, doc.file)
                self.docs.addItem(item)
            self.message.setText("")
        finally:
            self._loading = False
        self._update_size()

    def _new(self) -> None:
        profile = profiles.create(t("profile_default_name"), profiles.settings_from_config(self.cfg))
        self.reload(profile.slug)
        self.name.setFocus()
        self.name.selectAll()

    def _delete(self) -> None:
        if self.current is None:
            return
        slug = self.current.slug
        profiles.delete(slug)
        if self.cfg.get("profile") == slug:
            self.window_.use_profile(None)
        self.reload()

    # --- fields ---

    def _field_changed(self, *_args) -> None:
        if self._loading or self.current is None:
            return
        p = self.current
        p.name = self.name.text().strip() or p.name
        for key, (combo, keys) in self.combos.items():
            p.settings[key] = keys[max(0, combo.currentIndex())]
        p.settings["context"] = self.topic.text()
        p.settings["glossary"] = self.terms.toPlainText()
        p.notes = self.notes.toPlainText()
        profiles.save(p)
        self._dirty_slugs.add(p.slug)
        item = self.list.currentItem()
        if item is not None and item.text() != p.name:
            item.setText(p.name)
        self._update_size()

    def _doc_toggled(self, item: QListWidgetItem) -> None:
        if self._loading or self.current is None:
            return
        for doc in self.current.docs:
            if doc.file == item.data(Qt.ItemDataRole.UserRole):
                doc.enabled = item.checkState() == Qt.CheckState.Checked
        profiles.save(self.current)
        self._dirty_slugs.add(self.current.slug)
        self._update_size()

    def _add_docs(self) -> None:
        if self.current is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, t("profile_add_doc"), "", t("profile_doc_filter"))
        errors = []
        for path in paths:
            try:
                profiles.import_document(self.current, Path(path))
            except ValueError as e:
                errors.append(t("profile_unsupported", name=e))
            except Exception as e:
                errors.append(str(e))
        self._dirty_slugs.add(self.current.slug)
        self._show(self.current)
        self.message.setText("\n".join(errors))

    def _remove_doc(self) -> None:
        item = self.docs.currentItem()
        if self.current is None or item is None:
            return
        for doc in list(self.current.docs):
            if doc.file == item.data(Qt.ItemDataRole.UserRole):
                profiles.remove_document(self.current, doc)
        self._dirty_slugs.add(self.current.slug)
        self._show(self.current)

    def _update_size(self) -> None:
        if self.current is None:
            self.size.setText("")
            return
        chars = profiles.total_chars(self.current)
        limit = profiles.MAX_CHARS
        text = t("profile_size", chars=f"{chars:,}".replace(",", " "), limit=f"{limit:,}".replace(",", " "))
        if chars > limit:
            text += " " + t("profile_too_big")
        self.size.setText(text)
        self.size.setStyleSheet(f"color: {ERR_C if chars > limit else MUTED};")

    def _use(self) -> None:
        if self.current is not None:
            self._dirty_slugs.discard(self.current.slug)
            self.window_.use_profile(self.current.slug)

    def _flush(self) -> None:
        """Edits reach the running conversation only when the window closes, not on every keystroke."""
        if self.cfg.get("profile") in self._dirty_slugs:
            self.window_.profile_changed()
        self._dirty_slugs.clear()

    def hideEvent(self, event):
        self._flush()
        super().hideEvent(event)
