"""Composition root for Companion memory and reading services."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
import hashlib
import html
import io
import os
from pathlib import Path
import threading
import time
from typing import Any
import urllib.request

from core.character_profile import active_memory_namespace
from core.memory import AsyncMemoryWriter, HostMemoryExtractor, MemoryContextProvider, MemoryStore
from core.memory.extractor import Extractor
from core.reading import (
    ReadingChunk,
    ReadingContext,
    ReadingEventServer,
    ReadingSessionStore,
    SpoilerGuard,
)

from .active_reading import ActiveReading, ActiveReadingStore
from .context import CompanionContextBuilder
from .scenes import SceneBinding, SceneStore


class CompanionRuntime:
    """Owns Companion-local services without owning the Host chat loop."""

    def __init__(
        self,
        root: str | Path,
        *,
        extractor: Extractor | None = None,
        memory_limit: int = 5,
        reading_server_port: int = 17878,
    ):
        self.root = Path(root).expanduser().resolve()
        self.memory = MemoryStore.open(self.root / "memory")
        self.reading = ReadingSessionStore.open(self.root / "reading")
        self.provider = MemoryContextProvider(
            self.memory,
            dynamic_namespaces=lambda: (active_memory_namespace(),),
            limit=memory_limit,
        )
        self.context = CompanionContextBuilder(
            self.provider,
            self.reading,
            spoiler_guard=SpoilerGuard(),
        )
        self.writer = AsyncMemoryWriter(
            self.memory,
            extractor
            or HostMemoryExtractor(character_namespace=active_memory_namespace),
        )
        self.scenes = SceneStore(self.root / "scene.json")
        self.active_reading_store = ActiveReadingStore(self.root / "active_reading.json")
        self._restore_active_reading()
        self._comic_lock = threading.RLock()
        self._comic_cache: dict[str, str] = {}
        self.reading_server = ReadingEventServer(
            self.reading,
            port=reading_server_port,
            comic_handler=self._handle_comic_chapter,
            on_context_changed=self._activate_reading_context,
        )
        self._game_context_provider = None
        self._closed = False
        self._reading_maintenance_stop = threading.Event()
        self._reading_maintenance_started = False

    @staticmethod
    def _active_source_block(source: ActiveReading) -> str:
        lines = [
            "<active_source>",
            f"kind={html.escape(source.kind)}",
            f"book_id={html.escape(source.book_id)}",
            f"title={html.escape(source.title)}",
            (
                "instruction=This is the source the user is currently viewing. "
                "Answer from this source first. Other saved memory is background and "
                "must not be presented as the current scene unless the user asks."
            ),
            "</active_source>",
        ]
        return "\n".join(lines)

    @staticmethod
    def _source_switch_block(previous: ActiveReading | None, current: ActiveReading) -> str:
        previous_kind = html.escape(previous.kind) if previous is not None else "none"
        previous_id = html.escape(previous.book_id or previous.title) if previous is not None else ""
        current_kind = html.escape(current.kind)
        current_id = html.escape(current.book_id or current.title)
        return "\n".join(
            [
                "<source_switch>",
                f"previous_kind={previous_kind}",
                f"previous_id={previous_id}",
                f"current_kind={current_kind}",
                f"current_id={current_id}",
                (
                    "instruction=The active source just changed. Respond according to the current source. "
                    "Do not continue the previous source's topic unless the user explicitly asks to return to it."
                ),
                "</source_switch>",
            ]
        )

    def context_block(
        self,
        query: str,
        *,
        book_id: str | None = None,
        chunks: Sequence[ReadingChunk] = (),
        include_activity_memory: bool = True,
        prior_user_messages: Sequence[str] = (),
    ) -> str:
        """Build context from the explicitly selected source, not the newest one.

        An explicit ``book_id`` remains available for direct callers and tests.
        The chat adapter omits it, so the persisted active source is authoritative.
        """
        source = self.active_reading_store.current()
        explicit_book_id = str(book_id or "").strip()
        if explicit_book_id:
            effective_book_id = explicit_book_id
            reading_context = self.reading.get_context(effective_book_id)
            effective_kind = reading_context.kind if reading_context is not None else ""
        elif source is not None:
            effective_kind = source.kind
            effective_book_id = "" if effective_kind in {"comic", "game"} else source.book_id
        else:
            effective_kind = ""
            effective_book_id = ""

        scene_source = effective_kind in {"comic", "game"}
        if scene_source:
            effective_book_id = ""
        if effective_book_id and not chunks:
            chunks = tuple(self.reading.list_chunks(effective_book_id))

        context_block = self.context.build(
            query,
            book_id=effective_book_id or None,
            chunks=chunks,
            include_activity_memory=include_activity_memory and not scene_source,
            prior_user_messages=prior_user_messages,
        )
        blocks = [self.scenes.context_block(query), context_block] if scene_source else [context_block]
        provider = self._game_context_provider if effective_kind == "game" else None
        if provider is not None:
            try:
                provided = str(provider() or "").strip()
            except Exception:
                provided = ""
            if provided:
                blocks.append(provided)

        active_source = source
        if active_source is None and effective_book_id:
            reading_context = self.reading.get_context(effective_book_id)
            if reading_context is not None:
                active_source = ActiveReading(
                    kind=reading_context.kind,
                    book_id=reading_context.book_id,
                    title=reading_context.current_chapter,
                )
        source_prefix: list[str] = []
        switch_notice = self.active_reading_store.consume_switch()
        if switch_notice is not None:
            source_prefix.append(self._source_switch_block(*switch_notice))
        if active_source is not None:
            source_prefix.append(self._active_source_block(active_source))
        return "\n\n".join(block for block in [*source_prefix, *blocks] if block)

    def set_game_context_provider(self, provider) -> None:
        """Attach the live VN/Galgame hook as a read-only Game Companion source."""
        self._game_context_provider = provider

    def bind_scene(self, kind: str, *, window_handle: str = "", title: str = "", process_name: str = "") -> SceneBinding:
        scene = self.scenes.bind(
            kind,
            window_handle=window_handle,
            title=title,
            process_name=process_name,
        )
        self.activate_reading(scene.kind, title=scene.title)
        return scene

    def close_scene(self) -> None:
        self.scenes.close()
        source = self.active_reading_store.current()
        if source is not None and source.kind in {"comic", "game"}:
            self.active_reading_store.clear(source.kind)

    def scene_status(self) -> dict[str, object]:
        return self.scenes.status()

    def record_scene_capture(self, description: str) -> SceneBinding | None:
        return self.scenes.record_capture(description)

    def remember_scene_turn(self, user_message: str, assistant_message: str) -> None:
        self.scenes.append_turn(user_message, assistant_message)

    def _restore_active_reading(self) -> None:
        """Seed each independent slot while preserving the current focus."""
        store = self.active_reading_store
        # Seed both text lanes independently so an older Zotero item is not
        # lost when the latest browser session happened to be a web page.
        for kind in ("zotero", "web", "novel", "browser", "selection"):
            latest = self.reading.latest_context(kind=kind)
            if latest is None:
                continue
            latest_slot = store.slot_for_kind(latest.kind)
            if store.source(latest_slot) is None:
                store.set_source(
                    latest_slot,
                    latest.kind,
                    book_id=latest.book_id,
                    title=latest.current_chapter,
                )

        scene = self.scenes.current()
        scene_slot = ""
        if scene is not None and scene.kind in {"comic", "game"}:
            scene_slot = store.slot_for_kind(scene.kind)
            if store.source(scene_slot) is None:
                store.set_source(scene_slot, scene.kind, title=scene.title)

        if not store.focus():
            if scene_slot and store.source(scene_slot) is not None:
                store.focus_source(scene_slot)
            elif store.zotero() is not None:
                store.focus_source("zotero")
            elif store.web() is not None:
                store.focus_source("web")

    def activate_reading(self, kind: str, *, book_id: str = "", title: str = "") -> ActiveReading:
        return self.active_reading_store.activate(kind, book_id=book_id, title=title)

    def active_reading_source(self) -> ActiveReading | None:
        return self.active_reading_store.current()

    def active_reading_book_id(self) -> str:
        source = self.active_reading_store.current()
        if source is None or source.kind in {"comic", "game"}:
            return ""
        return source.book_id

    def _activate_reading_context(self, context: ReadingContext) -> ActiveReading:
        return self.activate_reading(
            context.kind,
            book_id=context.book_id,
            title=context.current_chapter,
        )

    def _handle_comic_chapter(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Turn one captured comic chapter into a bounded, spoiler-safe summary."""
        images = payload.get("images")
        if not isinstance(images, list) or not images:
            raise ValueError("comic chapter requires images")
        usable = [
            item
            for item in images
            if isinstance(item, dict) and (item.get("data_url") or item.get("url"))
        ][:40]
        if not usable:
            raise ValueError("comic chapter images are empty")
        book_id = str(payload.get("book_id") or "").strip()
        chapter = str(payload.get("chapter") or "").strip() or book_id or "comic"
        if not book_id:
            digest = hashlib.sha1(chapter.encode("utf-8", "ignore")).hexdigest()[:16]
            book_id = f"comic:{digest}"
        next_url = str(payload.get("next_url") or "").strip()
        cache_key = self._comic_cache_key(chapter, usable)
        with self._comic_lock:
            cached = self._comic_cache.get(cache_key)
        if cached:
            self._record_comic_chapter(book_id, chapter, cached, next_url)
            return {
                "ok": True,
                "book_id": book_id,
                "chapter": chapter,
                "summary": cached,
                "pages_used": len(usable),
                "next_url": next_url,
                "cached": True,
            }
        image_base64 = self._comic_montage_base64(usable)
        if not image_base64:
            raise ValueError("comic chapter images could not be decoded")
        from llm.qwen_client import qwen_vision_describe

        described = qwen_vision_describe(
            image_base64,
            prompt=(
                f"你正在阅读一话漫画。图片按阅读顺序拼成了本章总览（共 {len(usable)} 页）。"
                "请用中文概括本话：1) 本话剧情摘要；2) 出场角色；3) 关键事件；4) 重要对白。"
                "只描述本话可见内容，不推测下一话。控制在 500 字以内。"
            ),
            model=os.environ.get("AMADEUS_COMIC_VL_MODEL", "").strip() or None,
            max_tokens=600,
            timeout=60.0,
        )
        summary = str((described or {}).get("description") or "").strip()
        if not summary:
            raise RuntimeError("vision model returned no comic chapter summary")
        self._record_comic_chapter(book_id, chapter, summary, next_url)
        with self._comic_lock:
            self._comic_cache[cache_key] = summary
        return {
            "ok": True,
            "book_id": book_id,
            "chapter": chapter,
            "summary": summary,
            "pages_used": len(usable),
            "next_url": next_url,
        }

    def _record_comic_chapter(
        self,
        book_id: str,
        chapter: str,
        summary: str,
        next_url: str,
    ) -> None:
        self.scenes.bind("comic", title=chapter, window_handle="browser", process_name="browser")
        self.scenes.record_chapter(summary, future_ref=next_url)
        self.activate_reading("comic", book_id=book_id, title=chapter)

    @staticmethod
    def _comic_cache_key(chapter: str, images: list[dict[str, Any]]) -> str:
        digest = hashlib.sha1()
        digest.update(str(chapter).encode("utf-8", "ignore"))
        for item in images:
            value = str(item.get("url") or item.get("data_url") or "")
            digest.update(hashlib.sha1(value.encode("utf-8", "ignore")).digest())
        return digest.hexdigest()

    @staticmethod
    def _comic_image_bytes(item: dict[str, Any]) -> bytes:
        data_url = str(item.get("data_url") or "")
        if data_url.startswith("data:") and "," in data_url:
            try:
                return base64.b64decode(data_url.split(",", 1)[1])
            except Exception:
                return b""
        url = str(item.get("url") or "")
        if not url:
            return b""
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.read(12 * 1024 * 1024)
        except Exception:
            return b""

    @classmethod
    def _comic_montage_base64(cls, images: list[dict[str, Any]]) -> str:
        from PIL import Image

        frames = []
        for item in images:
            raw = cls._comic_image_bytes(item)
            if not raw:
                continue
            try:
                image = Image.open(io.BytesIO(raw)).convert("RGB")
            except Exception:
                continue
            width, height = image.size
            if width <= 0 or height <= 0:
                continue
            target_width = 360
            if width != target_width:
                target_height = max(1, round(height * target_width / width))
                image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            frames.append(image)
        if not frames:
            return ""

        columns = 3
        cell_width = 360
        rows = (len(frames) + columns - 1) // columns
        row_heights = []
        for row in range(rows):
            row_frames = frames[row * columns:(row + 1) * columns]
            row_heights.append(max(frame.height for frame in row_frames))

        montage = Image.new(
            "RGB",
            (columns * cell_width, max(1, sum(row_heights))),
            "white",
        )
        y = 0
        for row, row_height in enumerate(row_heights):
            for column, frame in enumerate(frames[row * columns:(row + 1) * columns]):
                montage.paste(frame, (column * cell_width, y))
            y += row_height

        max_width = 1080
        max_height = 8192
        if montage.width > max_width or montage.height > max_height:
            scale = min(max_width / montage.width, max_height / montage.height)
            montage = montage.resize(
                (
                    max(1, round(montage.width * scale)),
                    max(1, round(montage.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        montage.save(buffer, format="JPEG", quality=78, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def ingest_reading_event(self, event: Mapping[str, object]):
        context = self.reading.update_from_event(event)
        self._activate_reading_context(context)
        return context

    def remember_turn(
        self,
        book_id: str,
        *,
        user_message: str,
        assistant_message: str,
        selected_excerpt: str = "",
        referenced_chunks: Sequence[str] = (),
    ) -> None:
        self.context.remember_turn(
            book_id,
            user_message=user_message,
            assistant_message=assistant_message,
            selected_excerpt=selected_excerpt,
            referenced_chunks=referenced_chunks,
        )
        conversation = (
            f"User: {user_message}\n"
            f"Assistant: {assistant_message}\n"
            f"Selected excerpt: {selected_excerpt}"
        )
        self.writer.submit(conversation)

    def remember_conversation(
        self,
        *,
        user_message: str,
        assistant_message: str,
        book_id: str | None = None,
        selected_excerpt: str = "",
        referenced_chunks: Sequence[str] = (),
    ) -> None:
        self.scenes.append_turn(user_message, assistant_message)
        if book_id:
            self.remember_turn(
                book_id,
                user_message=user_message,
                assistant_message=assistant_message,
                selected_excerpt=selected_excerpt,
                referenced_chunks=referenced_chunks,
            )
            return
        self.writer.submit(
            f"User: {user_message}\nAssistant: {assistant_message}"
        )

    def start_reading_server(self) -> int:
        port = self.reading_server.start()
        if not self._reading_maintenance_started:
            self._reading_maintenance_started = True
            threading.Thread(
                target=self._reading_maintenance_loop,
                name="reading-retention",
                daemon=True,
            ).start()
        return port

    def _reading_maintenance_loop(self) -> None:
        while not self._reading_maintenance_stop.is_set():
            try:
                hours = max(
                    0.0,
                    float(
                        os.environ.get(
                            "AMADEUS_READING_FULLTEXT_TTL_HOURS", "6"
                        )
                    ),
                )
                if os.environ.get(
                    "AMADEUS_READING_RETENTION_ENABLED", "1"
                ).strip().lower() in {"1", "true", "yes", "on"}:
                    self.reading.compact_expired(
                        older_than_hours=hours,
                        kinds=("zotero",),
                        summarizer=self._summarize_reading_session,
                    )
            except Exception:
                pass
            self._reading_maintenance_stop.wait(3600.0)

    @staticmethod
    def _summarize_reading_session(context, chunks) -> str:
        source = "\n\n".join(
            f"[{chunk.chapter}]\n{chunk.text}" for chunk in chunks
        )[:60000]
        if not source.strip():
            return ""
        messages = [
            {
                "role": "system",
                "content": (
                    "请把论文全文整理成中文阅读报告。保留论文标题、核心问题、方法、"
                    "实验/结论和限制，文字简洁。最后增加“Companion 补充”，用 1-2 句"
                    "补充你认为值得记住的观察或关联。不要编造原文没有的事实。"
                ),
            },
            {
                "role": "user",
                "content": f"论文：{context.current_chapter}\n\n{source}",
            },
        ]
        from llm.client import remote_llm_messages_query

        return str(
            remote_llm_messages_query(
                messages,
                temperature=0.2,
                max_tokens=1400,
                timeout=60.0,
                json_output=False,
            )
        ).strip()
    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._reading_maintenance_stop.set()
        self.reading_server.stop()
        self.writer.close(timeout=timeout)
