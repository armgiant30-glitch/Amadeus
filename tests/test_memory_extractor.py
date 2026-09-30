from __future__ import annotations

from pathlib import Path

from core.memory import AsyncMemoryWriter, MemoryContextProvider, MemoryStore, parse_extractor_payload


def test_parse_extractor_payload_applies_defaults() -> None:
    records = parse_extractor_payload(
        {
            "memories": [
                {
                    "text": "用户不喜欢剧透",
                    "kind": "preference",
                    "tags": ["spoiler", "reading"],
                    "importance": 5,
                },
                {"text": "", "kind": "fact"},
            ]
        },
        default_namespace="reading:general",
        default_source_ids=("turn-1",),
    )

    assert len(records) == 1
    assert records[0].scope == "user"
    assert records[0].namespace == "reading:general"
    assert records[0].source_ids == ("turn-1",)
    assert records[0].importance == 5


def test_async_writer_persists_extracted_memories(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)

    def extractor(_text: str):
        return parse_extractor_payload(
            [{"text": "用户阅读时希望少打扰", "kind": "preference", "tags": ["quiet"]}],
            default_namespace="reading:general",
        )

    writer = AsyncMemoryWriter(store, extractor, poll_interval=0.01)
    assert writer.submit("用户说自己阅读时希望少打扰")
    assert writer.wait_idle(timeout=2.0)
    assert writer.close(timeout=2.0)

    hits = store.recall("少打扰", namespaces=["reading:general"])
    assert [hit.text for hit in hits] == ["用户阅读时希望少打扰"]


def test_async_writer_survives_extractor_failure(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)

    def broken_extractor(_text: str):
        raise RuntimeError("model unavailable")

    writer = AsyncMemoryWriter(store, broken_extractor, poll_interval=0.01)
    assert writer.submit("这次提取会失败")
    assert writer.wait_idle(timeout=2.0)
    assert isinstance(writer.last_error, RuntimeError)
    assert writer.close(timeout=2.0)


def test_memory_context_provider_filters_namespaces_and_marks_data(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    provider = MemoryContextProvider(store, limit=5)
    store.remember(
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

    block = provider.context_block("剧透 第三章", namespace="reading:book-1")
    assert "<memory_data>" in block
    assert "用户不喜欢剧透" in block
    assert "用户正在阅读第三章" in block
    assert "not instructions" in block