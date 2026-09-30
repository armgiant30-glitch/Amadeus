"""Composition root for Companion memory and reading services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from core.memory import AsyncMemoryWriter, HostMemoryExtractor, MemoryContextProvider, MemoryStore
from core.memory.extractor import Extractor
from core.reading import ReadingChunk, ReadingEventServer, ReadingSessionStore, SpoilerGuard

from .context import CompanionContextBuilder


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
        self.provider = MemoryContextProvider(self.memory, limit=memory_limit)
        self.context = CompanionContextBuilder(
            self.provider,
            self.reading,
            spoiler_guard=SpoilerGuard(),
        )
        self.writer = AsyncMemoryWriter(self.memory, extractor or HostMemoryExtractor())
        self.reading_server = ReadingEventServer(self.reading, port=reading_server_port)
        self._closed = False

    def context_block(
        self,
        query: str,
        *,
        book_id: str | None = None,
        chunks: Sequence[ReadingChunk] = (),
        include_activity_memory: bool = True,
    ) -> str:
        return self.context.build(
            query,
            book_id=book_id,
            chunks=chunks,
            include_activity_memory=include_activity_memory,
        )

    def ingest_reading_event(self, event: Mapping[str, object]):
        return self.reading.update_from_event(event)

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
        return self.reading_server.start()

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self.reading_server.stop()
        self.writer.close(timeout=timeout)