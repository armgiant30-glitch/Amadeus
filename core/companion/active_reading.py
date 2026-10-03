"""Separate reading and scene sources with one active focus pointer."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
import time
from typing import Any


_SLOTS = ("zotero", "web", "comic", "game")


@dataclass(frozen=True, slots=True)
class ActiveReading:
    """One source slot Companion can answer from."""

    kind: str
    book_id: str = ""
    title: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "book_id": self.book_id,
            "title": self.title,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActiveReading":
        kind = str(payload.get("kind") or "").strip().lower()
        if not kind:
            raise ValueError("active reading kind is required")
        return cls(
            kind=kind,
            book_id=str(payload.get("book_id") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            updated_at=float(payload.get("updated_at") or 0.0),
        )


class ActiveReadingStore:
    """Persist Zotero, web, comic and game slots independently.

    ``zotero`` and ``web`` keep their own latest reading session, ``comic``
    keeps the latest comic chapter and ``game`` remains a separate lane.
    ``focus`` is only a pointer to whichever slot chat should read now, so
    switching does not overwrite the other source's last state.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self._focus = ""
        self._sources: dict[str, ActiveReading] = {}
        self._switch_notice: tuple[ActiveReading | None, ActiveReading] | None = None
        self._load()

    @staticmethod
    def slot_for_kind(kind: str) -> str:
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind in {"zotero", "web", "comic", "game"}:
            return normalized_kind
        return "web"

    @staticmethod
    def _normalize_slot(slot: str) -> str:
        normalized_slot = str(slot or "").strip().lower()
        if normalized_slot not in _SLOTS:
            raise ValueError("active reading slot must be zotero, web, comic or game")
        return normalized_slot

    def _store_locked(
        self,
        slot: str,
        kind: str,
        *,
        book_id: str = "",
        title: str = "",
    ) -> ActiveReading:
        normalized_kind = str(kind or "").strip().lower()
        if not normalized_kind:
            raise ValueError("active reading kind is required")
        source = ActiveReading(
            kind=normalized_kind,
            book_id=str(book_id or "").strip(),
            title=str(title or "").strip(),
        )
        self._sources[slot] = source
        return source

    def set_source(
        self,
        slot: str,
        kind: str,
        *,
        book_id: str = "",
        title: str = "",
    ) -> ActiveReading:
        """Update one slot without changing which slot is currently focused."""
        normalized_slot = self._normalize_slot(slot)
        with self._lock:
            source = self._store_locked(
                normalized_slot,
                kind,
                book_id=book_id,
                title=title,
            )
            self._save_locked()
            return source

    def activate(
        self,
        kind: str,
        *,
        book_id: str = "",
        title: str = "",
    ) -> ActiveReading:
        """Update the matching slot and make it the active focus."""
        slot = self.slot_for_kind(kind)
        with self._lock:
            previous = self.current()
            source = self._store_locked(
                slot,
                kind,
                book_id=book_id,
                title=title,
            )
            self._focus = slot
            same_source = (
                previous is not None
                and previous.kind == source.kind
                and previous.book_id == source.book_id
                and previous.title == source.title
            )
            self._switch_notice = None if same_source else (previous, source)
            self._save_locked()
            return source

    def focus_source(self, slot: str) -> ActiveReading | None:
        """Switch focus to an already stored slot without re-sending content."""
        normalized_slot = self._normalize_slot(slot)
        with self._lock:
            source = self._sources.get(normalized_slot)
            if source is None:
                return None
            previous = self.current()
            self._focus = normalized_slot
            self._switch_notice = (previous, source) if previous != source else None
            self._save_locked()
            return source

    def current(self) -> ActiveReading | None:
        with self._lock:
            return self._sources.get(self._focus)

    def consume_switch(self) -> tuple[ActiveReading | None, ActiveReading] | None:
        with self._lock:
            notice = self._switch_notice
            self._switch_notice = None
            return notice

    def source(self, slot: str) -> ActiveReading | None:
        normalized_slot = self._normalize_slot(slot)
        with self._lock:
            return self._sources.get(normalized_slot)

    def zotero(self) -> ActiveReading | None:
        return self.source("zotero")

    def web(self) -> ActiveReading | None:
        return self.source("web")

    def reading(self) -> ActiveReading | None:
        """Compatibility alias for the last text-web reading source."""
        return self.web() or self.zotero()

    def comic(self) -> ActiveReading | None:
        return self.source("comic")

    def game(self) -> ActiveReading | None:
        return self.source("game")

    def focus(self) -> str:
        with self._lock:
            return self._focus

    def clear(self, kind: str | None = None) -> None:
        """Clear one slot, or all state when ``kind`` is omitted."""
        with self._lock:
            if kind is None:
                self._focus = ""
                self._sources.clear()
            else:
                slot = self.slot_for_kind(kind)
                self._sources.pop(slot, None)
                if self._focus == slot:
                    self._focus = next(
                        (candidate for candidate in _SLOTS if candidate in self._sources),
                        "",
                    )
            self._save_locked()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return

        sources_payload = payload.get("sources")
        if isinstance(sources_payload, dict):
            migrated_focus = ""
            for raw_slot, value in sources_payload.items():
                if not isinstance(value, dict):
                    continue
                try:
                    source = ActiveReading.from_dict(value)
                except (TypeError, ValueError):
                    continue
                slot = str(raw_slot or "").strip().lower()
                if slot == "reading":
                    slot = self.slot_for_kind(source.kind)
                    migrated_focus = slot
                if slot not in _SLOTS:
                    continue
                self._sources[slot] = source
            focus = str(payload.get("focus") or "").strip().lower()
            if focus == "reading":
                focus = migrated_focus or ("web" if "web" in self._sources else "zotero")
            self._focus = focus if focus in self._sources else next(
                (candidate for candidate in _SLOTS if candidate in self._sources),
                "",
            )
            return

        # Migrate the previous single-source file without losing the focus.
        legacy = payload.get("active_reading")
        if not isinstance(legacy, dict):
            return
        try:
            source = ActiveReading.from_dict(legacy)
        except (TypeError, ValueError):
            return
        slot = self.slot_for_kind(source.kind)
        self._sources[slot] = source
        self._focus = slot

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "focus": self._focus or None,
            "sources": {
                slot: self._sources[slot].to_dict()
                for slot in _SLOTS
                if slot in self._sources
            },
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.path)
