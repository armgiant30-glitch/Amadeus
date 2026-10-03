from __future__ import annotations

from pathlib import Path

from core.companion import CompanionContextBuilder, CompanionRuntime
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
def test_chat_runtime_has_optional_extra_context_for_companion() -> None:
    import inspect
    from core.chat_runtime import ChatRuntime, _TurnState, _turn_role_grounding

    signature = inspect.signature(ChatRuntime.stream_llm_query)
    assert "extra_context" in signature.parameters

    state = _TurnState(
        gui_callback=None,
        extra_context="<memory_data>用户不喜欢剧透</memory_data>",
    )
    assert "用户不喜欢剧透" in _turn_role_grounding(state)
def test_companion_runtime_composes_services(tmp_path: Path) -> None:
    def extractor(_text: str):
        return parse_extractor_payload(
            [{"text": "用户不喜欢剧透", "kind": "preference"}],
            default_namespace="general",
        )

    runtime = CompanionRuntime(tmp_path, extractor=extractor, reading_server_port=0)
    try:
        runtime.ingest_reading_event(
            {
                "book_id": "book-1",
                "chapter": "第三章",
                "cursor": 100,
                "page": 42,
            }
        )
        runtime.remember_turn(
            "book-1",
            user_message="我不喜欢剧透",
            assistant_message="记住了",
        )
        assert runtime.writer.wait_idle(timeout=2.0)

        block = runtime.context_block("剧透", book_id="book-1")
        assert "用户不喜欢剧透" in block
        assert "book-1" in block
    finally:
        runtime.close()

def test_comic_scene_does_not_get_overridden_by_zotero(tmp_path: Path) -> None:
    from core.companion import CompanionRuntime

    runtime = CompanionRuntime(tmp_path, reading_server_port=0)
    try:
        runtime.ingest_reading_event(
            {
                "book_id": "zotero:paper-1",
                "kind": "zotero",
                "chapter": "译文 Markdown",
                "cursor": 20,
                "text": "这是 Zotero 译文正文，不应该覆盖当前漫画场景。",
            }
        )
        runtime.bind_scene("comic", title="小爱漫画", window_handle="browser", process_name="browser")
        runtime.record_scene_capture("当前漫画页摘要：两位少女在家里讨论同住。")
        block = runtime.context_block("这一页讲了什么？")
        assert "当前漫画页摘要" in block
        assert "<scene_context>" in block
        assert "<reading_context>" not in block
        assert "这是 Zotero 译文正文" not in block
    finally:
        runtime.close(timeout=2.0)
