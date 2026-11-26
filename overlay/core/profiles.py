"""Profiles: a saved scenario (an interview at X, a lecture course) with its settings, notes and documents.

Stored as plain files under profiles/<slug>/: profile.json and docs/*.txt. These are the user's own
documents, not conversations, so they may live on disk; the folder is ignored by git.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from overlay.config import APP_DIR

log = logging.getLogger(__name__)

PROFILES_DIR = APP_DIR / "profiles"
MAX_CHARS = 30_000
TEXT_EXT = (".txt", ".md", ".markdown", ".text")
SETTING_KEYS = (
    "mode",
    "model",
    "effort",
    "audio_source",
    "speech_language",
    "answer_language",
    "context",
    "glossary",
)
PROFILE_INTRO = (
    "The user set up this profile for the conversation. Treat the notes and documents as facts about "
    "the user and the situation, and prefer them over guesses: answer from the user's actual experience, "
    "with the names, projects and numbers found here."
)


@dataclass
class Document:
    file: str
    title: str
    enabled: bool = True


@dataclass
class Profile:
    slug: str
    name: str
    settings: dict = field(default_factory=dict)
    notes: str = ""
    docs: list[Document] = field(default_factory=list)
    updated: str = ""


def folder(profile: Profile | str, root: Path | None = None) -> Path:
    root = root or PROFILES_DIR
    return root / (profile if isinstance(profile, str) else profile.slug)


def slugify(name: str, root: Path | None = None) -> str:
    root = root or PROFILES_DIR
    base = re.sub(r"[^\w-]+", "-", name.strip().lower(), flags=re.UNICODE).strip("-")[:40] or "profile"
    slug, n = base, 2
    while (root / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def list_profiles(root: Path | None = None) -> list[Profile]:
    root = root or PROFILES_DIR
    if not root.is_dir():
        return []
    found = [p for p in (load(d.name, root) for d in root.iterdir() if d.is_dir()) if p is not None]
    return sorted(found, key=lambda p: p.name.lower())


def load(slug: str, root: Path | None = None) -> Profile | None:
    root = root or PROFILES_DIR
    path = folder(slug, root) / "profile.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        docs = [Document(**d) for d in data.get("docs", []) if isinstance(d, dict) and "file" in d]
        settings = {k: v for k, v in (data.get("settings") or {}).items() if k in SETTING_KEYS}
        return Profile(
            slug, str(data.get("name") or slug), settings, str(data.get("notes") or ""), docs, data.get("updated", "")
        )
    except Exception:
        log.exception("profile %s is unreadable", slug)
        return None


load_profile = load


def save(profile: Profile, root: Path | None = None) -> None:
    root = root or PROFILES_DIR
    profile.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = folder(profile, root) / "profile.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    data.pop("slug")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def create(name: str, settings: dict | None = None, root: Path | None = None) -> Profile:
    root = root or PROFILES_DIR
    profile = Profile(slugify(name, root), name.strip() or "profile", dict(settings or {}))
    save(profile, root)
    return profile


def delete(slug: str, root: Path | None = None) -> None:
    root = root or PROFILES_DIR
    shutil.rmtree(folder(slug, root), ignore_errors=True)


def settings_from_config(cfg: dict) -> dict:
    return {k: cfg[k] for k in SETTING_KEYS if k in cfg}


def apply_to_config(profile: Profile, cfg: dict) -> None:
    for key in SETTING_KEYS:
        if key in profile.settings:
            cfg[key] = profile.settings[key]
    cfg["profile"] = profile.slug


# --- documents ---


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXT:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    raise ValueError(path.name)


def import_document(profile: Profile, source: Path, root: Path | None = None) -> Document:
    """Copies the file's text into the profile. ValueError for a format that is not supported."""
    root = root or PROFILES_DIR
    text = _read_text(source).strip()
    docs_dir = folder(profile, root) / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\- ]+", "", source.stem).strip() or "document"
    name, n = f"{stem}.txt", 2
    while (docs_dir / name).exists():
        name, n = f"{stem}-{n}.txt", n + 1
    (docs_dir / name).write_text(text, encoding="utf-8")
    doc = Document(name, source.stem)
    profile.docs.append(doc)
    save(profile, root)
    return doc


def remove_document(profile: Profile, doc: Document, root: Path | None = None) -> None:
    root = root or PROFILES_DIR
    (folder(profile, root) / "docs" / doc.file).unlink(missing_ok=True)
    profile.docs = [d for d in profile.docs if d.file != doc.file]
    save(profile, root)


def document_text(profile: Profile, doc: Document, root: Path | None = None) -> str:
    root = root or PROFILES_DIR
    path = folder(profile, root) / "docs" / doc.file
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def total_chars(profile: Profile, root: Path | None = None) -> int:
    root = root or PROFILES_DIR
    return len(profile.notes) + sum(len(document_text(profile, d, root)) for d in profile.docs if d.enabled)


def system_block(profile: Profile | None, root: Path | None = None, limit: int = MAX_CHARS) -> str:
    """The second system block, cached by the API; empty when the profile adds nothing."""
    root = root or PROFILES_DIR
    if profile is None:
        return ""
    parts: list[str] = []
    budget = limit
    notes = profile.notes.strip()
    if notes:
        parts.append(f"<notes>\n{notes[:budget]}\n</notes>")
        budget -= len(notes)
    trimmed = False
    for doc in profile.docs:
        if not doc.enabled:
            continue
        text = document_text(profile, doc, root).strip()
        if not text:
            continue
        if budget <= 0:
            trimmed = True
            break
        if len(text) > budget:
            text, trimmed = text[:budget], True
        parts.append(f'<document title="{doc.title}">\n{text}\n</document>')
        budget -= len(text)
    if not parts:
        return ""
    if trimmed:
        parts.append("[… the profile is over its size limit; the rest of the documents was cut]")
    body = "\n\n".join(parts)
    return f'<profile name="{profile.name}">\n{PROFILE_INTRO}\n\n{body}\n</profile>'
