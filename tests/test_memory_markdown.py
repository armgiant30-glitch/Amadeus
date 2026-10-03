from __future__ import annotations

from pathlib import Path

import pytest

from core.memory import MemoryContextProvider, MemoryRecord, MemoryStore
from core.memory.markdown_document import document_hash, parse_document, render_document


def make_record(
    text: str,
    *,
    memory_id: str,
    kind: str = "preference",
    namespace: str = "general",
) -> MemoryRecord:
    return MemoryRecord.create(
        memory_id=memory_id,
        text=text,
        kind=kind,
        scope="user",
        namespace=namespace,
        importance=4,
        confidence=0.9,
    )


def test_markdown_roundtrip_preserves_metadata() -> None:
    record = make_record("用户不喜欢剧透", memory_id="m1")
    entries = parse_document(render_document([record]))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.memory_id == "m1"
    assert entry.text == "用户不喜欢剧透"
    assert entry.kind == "preference"
    assert entry.namespace == "general"
    assert entry.importance == 4
    assert abs(entry.confidence - 0.9) < 1e-9


def test_manual_add_update_delete_syncs_to_store(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    original = make_record("用户不喜欢剧透", memory_id="m1")
    store.remember([original])

    document = store.markdown_path.read_text(encoding="utf-8")
    document = document.replace("用户不喜欢剧透", "用户强烈不喜欢剧透")
    document += "\n## fact\n\n- 用户喜欢黑咖啡\n"
    store.markdown_path.write_text(document, encoding="utf-8")

    store.write_projections()

    updated = store.get("m1")
    assert updated is not None
    assert updated.text == "用户强烈不喜欢剧透"
    added = store.recall("黑咖啡", namespaces=["general"], scopes=["user"])
    assert len(added) == 1
    assert added[0].source == "manual_markdown"

    document = store.markdown_path.read_text(encoding="utf-8")
    document = "\n".join(
        line for line in document.splitlines() if "用户强烈不喜欢剧透" not in line
    ) + "\n"
    store.markdown_path.write_text(document, encoding="utf-8")
    store.write_projections()

    revoked = store.get("m1")
    assert revoked is not None
    assert revoked.status == "revoked"
    assert store.recall("剧透", namespaces=["general"], scopes=["user"]) == []


def test_manual_durable_sync_does_not_revoke_episode(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember(
        [
            make_record("用户不喜欢剧透", memory_id="pref"),
            make_record(
                "用户刚看完了第一章",
                memory_id="episode",
                kind="episode",
            ),
        ]
    )
    document = store.markdown_path.read_text(encoding="utf-8")
    document = "\n".join(
        line for line in document.splitlines() if "用户不喜欢剧透" not in line
    ) + "\n"
    store.markdown_path.write_text(document, encoding="utf-8")
    store.write_projections()

    assert store.get("pref").status == "revoked"
    assert store.get("episode").status == "active"


def test_unsynced_edit_is_preserved_when_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember([make_record("用户不喜欢剧透", memory_id="m1")])
    document = store.markdown_path.read_text(encoding="utf-8") + "\n- 人工未同步内容\n"
    store.markdown_path.write_text(document, encoding="utf-8")

    def fail_parse():
        raise ValueError("cannot parse")

    monkeypatch.setattr(store, "_read_markdown_entries", fail_parse)
    store.write_projections()

    assert store.markdown_path.read_text(encoding="utf-8") == document


def test_projection_hash_tracks_generated_document(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember([make_record("用户不喜欢剧透", memory_id="m1")])
    document = store.markdown_path.read_text(encoding="utf-8")
    saved = store.markdown_hash_path.read_text(encoding="utf-8").strip()
    assert saved == document_hash(document)


def test_moving_bullet_between_sections_changes_kind() -> None:
    record = make_record("用户喜欢黑咖啡", memory_id="m1", kind="preference")
    document = render_document([record])
    document = document.replace("## preference", "## fact")
    entries = parse_document(document)
    assert entries[0].kind == "fact"


def test_manual_document_without_ids_is_imported_after_baseline(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember([make_record("用户不喜欢剧透", memory_id="m1")])
    store.markdown_path.write_text(
        "# Companion Memory\n\n## fact\n\n- 用户喜欢黑咖啡\n",
        encoding="utf-8",
    )
    store.write_projections()

    assert store.get("m1").status == "revoked"
    hits = store.recall("黑咖啡", namespaces=["general"], scopes=["user"])
    assert len(hits) == 1
    assert hits[0].text == "用户喜欢黑咖啡"


def test_update_texts_recomputes_hash_and_refreshes(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    original = make_record("Original English memory", memory_id="m1")
    store.remember([original])

    updated = store.update_texts({"m1": "翻译后的中文记忆"})

    assert len(updated) == 1
    assert updated[0].text == "翻译后的中文记忆"
    assert updated[0].content_hash != original.content_hash
    assert store.get("m1").text == "翻译后的中文记忆"
    assert "翻译后的中文记忆" in store.markdown_path.read_text(encoding="utf-8")


def test_parse_extractor_character_scope_maps_namespace() -> None:
    from core.memory import parse_extractor_payload

    records = parse_extractor_payload(
        [
            {"text": "用户喜欢黑咖啡", "kind": "preference", "memory_scope": "global"},
            {
                "text": "助手自称红莉栖",
                "kind": "fact",
                "memory_scope": "character",
            },
        ],
        character_namespace="character:kurisu",
    )
    assert records[0].namespace == "general"
    assert records[1].namespace == "character:kurisu"


def test_dynamic_namespace_isolated_from_other_character(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember(
        [
            make_record("全局助手偏好", memory_id="global"),
            make_record("红莉栖是助手", memory_id="kurisu", namespace="character:kurisu"),
            make_record("八千代是助手", memory_id="yachiyo", namespace="character:yachiyo"),
        ]
    )
    provider = MemoryContextProvider(
        store,
        dynamic_namespaces=lambda: ("character:kurisu",),
        limit=5,
    )
    block = provider.context_block("助手")
    assert "全局助手偏好" in block
    assert "红莉栖是助手" in block
    assert "八千代是助手" not in block


def test_character_document_is_separate_and_editable(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember(
        [
            make_record("用户喜欢黑咖啡", memory_id="global"),
            make_record(
                "助手自称红莉栖",
                memory_id="kurisu",
                namespace="character:kurisu",
            ),
        ]
    )
    global_document = store.markdown_path.read_text(encoding="utf-8")
    assert "用户喜欢黑咖啡" in global_document
    assert "助手自称红莉栖" not in global_document

    character_path = store.characters_dir / "character-kurisu.md"
    assert character_path.is_file()
    character_document = character_path.read_text(encoding="utf-8")
    assert "助手自称红莉栖" in character_document
    assert "用户喜欢黑咖啡" not in character_document
    assert character_path.with_name(character_path.name + ".sha256").is_file()

    character_document = character_document.replace("助手自称红莉栖", "红莉栖是助手")
    character_path.write_text(character_document, encoding="utf-8")
    store.write_projections()
    assert store.get("kurisu").text == "红莉栖是助手"


def test_update_namespaces_moves_record_and_refreshes(tmp_path: Path) -> None:
    store = MemoryStore.open(tmp_path)
    store.remember([make_record("助手自称红莉栖", memory_id="m1")])
    updated = store.update_namespaces({"m1": "character:kurisu"})
    assert len(updated) == 1
    assert store.get("m1").namespace == "character:kurisu"
    assert "助手自称红莉栖" not in store.markdown_path.read_text(encoding="utf-8")
    character_document = (store.characters_dir / "character-kurisu.md").read_text(
        encoding="utf-8"
    )
    assert "助手自称红莉栖" in character_document
    assert 'namespace="character:kurisu"' in character_document
