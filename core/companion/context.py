"""Compose reading state and long-term memory for the existing chat context."""

from __future__ import annotations

from collections.abc import Sequence
import html

from core.memory import MemoryContextProvider
from core.reading import ReadingChunk, ReadingSessionStore, SpoilerGuard


class CompanionContextBuilder:
    """Produces bounded context blocks without replacing the Host context builder."""

    def __init__(
        self,
        memory: MemoryContextProvider,
        reading: ReadingSessionStore,
        *,
        spoiler_guard: SpoilerGuard | None = None,
        recent_turn_limit: int = 6,
    ):
        self.memory = memory
        self.reading = reading
        self.spoiler_guard = spoiler_guard or SpoilerGuard()
        self.recent_turn_limit = max(0, min(int(recent_turn_limit), 20))

    def build(
        self,
        query: str,
        *,
        book_id: str | None = None,
        chunks: Sequence[ReadingChunk] = (),
        include_activity_memory: bool = True,
    ) -> str:
        blocks: list[str] = []
        reading_context = self.reading.get_context(book_id) if book_id else None
        if reading_context is not None:
            allowed = self.spoiler_guard.prepare_chunks(reading_context, chunks) if chunks else []
            blocks.append(self._render_reading(reading_context, allowed))
        scopes = ("user", "activity") if include_activity_memory else ("user",)
        namespace = reading_context.namespace if reading_context else None
        records = self.memory.recall(query, namespace=namespace, scopes=scopes)
        memory_block = self.memory.render(records)
        if memory_block:
            blocks.append(memory_block)
        return "\n\n".join(blocks)

    def remember_turn(
        self,
        book_id: str,
        *,
        user_message: str,
        assistant_message: str,
        selected_excerpt: str = "",
        referenced_chunks: Sequence[str] = (),
    ) -> None:
        self.reading.append_turn(
            book_id,
            user_message=user_message,
            assistant_message=assistant_message,
            selected_excerpt=selected_excerpt,
            referenced_chunks=referenced_chunks,
        )

    def _render_reading(self, context, chunks: Sequence[ReadingChunk]) -> str:
        lines = [
            "<reading_context>",
            f"book_id={html.escape(context.book_id)}",
            f"kind={html.escape(context.kind)}",
            f"chapter={html.escape(context.current_chapter)}",
            f"cursor={context.cursor}",
            f"spoiler_cursor={context.spoiler_cursor}",
        ]
        if context.current_page is not None:
            lines.append(f"page={context.current_page}")
        if chunks:
            lines.append("selected_chunks:")
            for chunk in chunks:
                lines.append(
                    f"- id={html.escape(chunk.id)} chapter={html.escape(chunk.chapter)} "
                    f"offset={chunk.start_offset}:{chunk.end_offset}\n"
                    f"{html.escape(chunk.text)}"
                )
        turns = self.reading.recent_turns(context.book_id, limit=self.recent_turn_limit)
        if turns:
            lines.append("recent_turns:")
            for turn in turns:
                lines.append(
                    f"- user: {html.escape(str(turn['user_message']))}\n"
                    f"  assistant: {html.escape(str(turn['assistant_message']))}"
                )
        lines.append("</reading_context>")
        return "\n".join(lines)