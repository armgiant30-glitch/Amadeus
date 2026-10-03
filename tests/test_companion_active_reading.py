"""Reading and scene sources are separate, persistent and explicitly focused."""

from __future__ import annotations

import sys
import types

from core.companion import CompanionRuntime


ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _zotero_event(book_id: str, text: str) -> dict[str, object]:
    return {
        "book_id": book_id,
        "kind": "zotero",
        "chapter": "译文 Markdown",
        "cursor": len(text),
        "text": text,
    }


def _web_event(book_id: str, text: str) -> dict[str, object]:
    return {
        "book_id": book_id,
        "kind": "web",
        "chapter": "网页章节",
        "cursor": len(text),
        "text": text,
    }


def test_active_source_switches_between_zotero_and_comic(tmp_path, monkeypatch) -> None:
    def fake_vision(image_base64: str, *, prompt: str = "", **kwargs):
        return {"description": "漫画第一话：主角在车站重逢，并发现一封旧信。"}

    fake_module = types.ModuleType("llm.qwen_client")
    fake_module.qwen_vision_describe = fake_vision
    monkeypatch.setitem(sys.modules, "llm.qwen_client", fake_module)
    runtime = CompanionRuntime(tmp_path, reading_server_port=0)
    try:
        runtime.ingest_reading_event(_zotero_event("zotero:paper-1", "第一篇论文正文"))
        assert runtime.active_reading_source().kind == "zotero"
        first = runtime.context_block("这篇论文讲了什么")
        assert "zotero:paper-1" in first
        assert "第一篇论文正文" in first

        runtime.ingest_reading_event(_web_event("browser:web-1", "第一篇网页正文"))
        assert runtime.active_reading_source().kind == "web"
        assert runtime.active_reading_store.zotero().book_id == "zotero:paper-1"
        web = runtime.context_block("这页网页讲了什么")
        assert "browser:web-1" in web
        assert "第一篇网页正文" in web
        assert "<reading_context>" in web
        assert "<scene_context>" not in web

        runtime._handle_comic_chapter(
            {
                "book_id": "book:comic-1",
                "chapter": "第一话 车站",
                "images": [{"data_url": ONE_PIXEL_PNG, "width": 1, "height": 1}],
            }
        )
        assert runtime.active_reading_source().kind == "comic"
        assert runtime.active_reading_store.zotero().book_id == "zotero:paper-1"
        assert runtime.active_reading_store.web().book_id == "browser:web-1"
        assert runtime.active_reading_store.comic().book_id == "book:comic-1"
        comic = runtime.context_block("这一话讲了什么")
        assert "漫画第一话" in comic
        assert "<scene_context>" in comic
        assert "<reading_context>" not in comic
        assert "第一篇论文正文" not in comic
        assert "第一篇网页正文" not in comic

        restored = runtime.active_reading_store.focus_source("zotero")
        assert restored is not None and restored.book_id == "zotero:paper-1"
        assert runtime.active_reading_source().kind == "zotero"
        second = runtime.context_block("这篇论文讲了什么")
        assert "zotero:paper-1" in second
        assert "第一篇论文正文" in second
        assert "第一篇网页正文" not in second
        assert "漫画第一话" not in second

        restored_web = runtime.active_reading_store.focus_source("web")
        assert restored_web is not None and restored_web.book_id == "browser:web-1"
        assert runtime.active_reading_source().kind == "web"
        third = runtime.context_block("这页网页讲了什么")
        assert "browser:web-1" in third
        assert "第一篇网页正文" in third
        assert "<reading_context>" in third
        assert "<scene_context>" not in third
        assert "漫画第一话" not in third

        focused_comic = runtime.active_reading_store.focus_source("comic")
        assert focused_comic is not None and focused_comic.book_id == "book:comic-1"
        assert runtime.active_reading_source().kind == "comic"
    finally:
        runtime.close(timeout=2.0)


def test_switch_notice_tells_model_the_current_source(tmp_path, monkeypatch) -> None:
    def fake_vision(image_base64: str, *, prompt: str = "", **kwargs):
        return {"description": "漫画第一话：文化祭前的校园日常。"}

    fake_module = types.ModuleType("llm.qwen_client")
    fake_module.qwen_vision_describe = fake_vision
    monkeypatch.setitem(sys.modules, "llm.qwen_client", fake_module)
    runtime = CompanionRuntime(tmp_path, reading_server_port=0)
    try:
        runtime.ingest_reading_event(_zotero_event("zotero:before-comic", "小说正文"))
        runtime.context_block("先看小说")
        runtime._handle_comic_chapter(
            {
                "book_id": "book:comic-switch",
                "chapter": "漫画第一话",
                "images": [{"data_url": ONE_PIXEL_PNG, "width": 1, "height": 1}],
            }
        )
        block = runtime.context_block("现在聊漫画")
        assert "<source_switch>" in block
        assert "previous_kind=zotero" in block
        assert "current_kind=comic" in block
        assert "<active_source>" in block
        assert "kind=comic" in block
    finally:
        runtime.close(timeout=2.0)


def test_active_source_survives_restart(tmp_path) -> None:
    runtime = CompanionRuntime(tmp_path, reading_server_port=0)
    try:
        runtime.ingest_reading_event(_zotero_event("zotero:restart", "重启后仍然有效的正文"))
        runtime.ingest_reading_event(_web_event("browser:restart", "重启后仍然有效的网页正文"))
        runtime.bind_scene("comic", title="小爱漫画")
    finally:
        runtime.close(timeout=2.0)

    reopened = CompanionRuntime(tmp_path, reading_server_port=0)
    try:
        source = reopened.active_reading_source()
        assert source is not None
        assert source.kind == "comic"
        assert reopened.active_reading_store.zotero().book_id == "zotero:restart"
        assert reopened.active_reading_store.web().book_id == "browser:restart"
        assert reopened.active_reading_store.comic().title == "小爱漫画"

        restored = reopened.active_reading_store.focus_source("zotero")
        assert restored is not None and restored.book_id == "zotero:restart"
        block = reopened.context_block("继续讲这篇论文")
        assert "zotero:restart" in block
        assert "重启后仍然有效的正文" in block

        restored_web = reopened.active_reading_store.focus_source("web")
        assert restored_web is not None and restored_web.book_id == "browser:restart"
        web_block = reopened.context_block("继续看网页")
        assert "browser:restart" in web_block
        assert "重启后仍然有效的网页正文" in web_block
    finally:
        reopened.close(timeout=2.0)
