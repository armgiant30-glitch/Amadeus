from __future__ import annotations

from pathlib import Path

from core.companion import CompanionContextBuilder
from core.memory import MemoryContextProvider, MemoryStore, parse_extractor_payload
from core.reading import ReadingChunk, ReadingContext, ReadingSessionStore


def test_companion_context_combines_reading_and_memory(tmp_path: Path) -> None:
    memory = MemoryStore.open(tmp_path / "memory")
    reading = ReadingSessionStore.open(tmp_path / "reading")
    provider = MemoryContextProvider(memory, limit=5)
    builder = CompanionContextBuilder(provider, reading)

    memory.remember(
        parse_extractor_payload(
            [
                {"text": "用户不喜欢剧透", "kind": "preference"},
                {
                    "text": "用户正在阅读第三章",
                    "kind": "story_state",
                    "scope": "activity",
                    "namespace": "reading:book-1",
                },
            ],
            default_namespace="general",
        )
    )
    reading.save_context(ReadingContext(book_id="book-1", cursor=100, spoiler_cursor=100))
    reading.append_turn(
        "book-1",
        user_message="继续总结",
        assistant_message="上一轮总结内容",
    )
    chunks = [
        ReadingChunk("c1", "第一章", 0, 100, "已经读到的内容"),
        ReadingChunk("c2", "第二章", 100, 200, "未来章节秘密"),
    ]

    block = builder.build("剧透 第三章", book_id="book-1", chunks=chunks)

    assert "<reading_context>" in block
    assert "已经读到的内容" in block
    assert "未来章节秘密" not in block
    assert "上一轮总结内容" in block
    assert "<memory_data>" in block
    assert "用户不喜欢剧透" in block
    assert "用户正在阅读第三章" in block


def test_companion_context_can_exclude_activity_memory(tmp_path: Path) -> None:
    memory = MemoryStore.open(tmp_path / "memory")
    reading = ReadingSessionStore.open(tmp_path / "reading")
    builder = CompanionContextBuilder(MemoryContextProvider(memory), reading)
    memory.remember(
        parse_extractor_payload(
            [
                {"text": "用户不喜欢剧透", "kind": "preference"},
                {
                    "text": "未来剧情状态",
                    "kind": "story_state",
                    "scope": "activity",
                    "namespace": "reading:book-1",
                },
            ],
            default_namespace="general",
        )
    )

    block = builder.build("剧透 未来剧情", book_id="book-1", include_activity_memory=False)

    assert "用户不喜欢剧透" in block
    assert "未来剧情状态" not in block