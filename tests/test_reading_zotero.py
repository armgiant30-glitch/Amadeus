from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from core.companion import CompanionContextBuilder
from core.memory import MemoryContextProvider, MemoryStore
from core.reading import ReadingChunk, ReadingContext, ReadingEventServer, ReadingSessionStore, ZoteroLocalClient


class FakeZotero(ZoteroLocalClient):
    def __init__(self):
        super().__init__(base_url="http://zotero.test/api/users/0", timeout=1)
        self.calls: list[str] = []

    def _request_json(self, path: str, query=None):
        self.calls.append(path)
        if path in {"/items", "/items/top"}:
            return [
                {
                    "key": "PAPER1",
                    "version": 3,
                    "data": {
                        "itemType": "journalArticle",
                        "title": "A Paper",
                        "creators": [{"firstName": "Ada", "lastName": "Lovelace"}],
                        "date": "2026",
                        "abstractNote": "Abstract text",
                        "tags": [{"tag": "agents"}],
                    },
                }
            ]
        if path == "/items/PAPER1":
            return self._request_json("/items")[0]
        if path == "/items/PAPER1/children":
            return [
                {
                    "key": "ATTACH1",
                    "data": {
                        "key": "ATTACH1",
                        "itemType": "attachment",
                        "contentType": "application/pdf",
                    },
                }
            ]
        if path == "/items/ATTACH1/fulltext":
            return {"content": "Full paper text. " * 20, "totalChars": 360}
        raise AssertionError(path)


def _request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_zotero_sync_maps_item_fulltext_to_reading_context(tmp_path: Path) -> None:
    store = ReadingSessionStore.open(tmp_path / "reading")
    client = FakeZotero()
    result = client.sync_item(store, "PAPER1", max_chars=4000)
    assert result["ok"] is True
    assert result["book_id"] == "zotero:PAPER1"
    assert result["attachment_key"] == "ATTACH1"
    assert result["item"]["title"] == "A Paper"
    context = store.get_context("zotero:PAPER1")
    assert context is not None
    assert context.kind == "zotero"
    assert context.current_chapter == "A Paper"
    assert context.cursor == result["chars"]
    chunks = store.list_chunks("zotero:PAPER1")
    assert chunks and "Full paper text" in chunks[0].text


def test_zotero_selected_text_wins_over_fulltext(tmp_path: Path) -> None:
    store = ReadingSessionStore.open(tmp_path / "reading")
    client = FakeZotero()
    result = client.sync_item(store, "PAPER1", selected_text="Selected sentence")
    assert result["chars"] == len("Selected sentence")
    assert store.list_chunks("zotero:PAPER1")[0].text == "Selected sentence"


def test_reading_server_exposes_zotero_endpoints(tmp_path: Path) -> None:
    store = ReadingSessionStore.open(tmp_path / "reading")
    activated = []
    server = ReadingEventServer(
        store,
        port=0,
        zotero_client=FakeZotero(),
        on_context_changed=activated.append,
    )
    port = server.start()
    base = f"http://127.0.0.1:{port}"
    try:
        status = _request_json(f"{base}/zotero/status")
        assert status["available"] is True
        items = _request_json(f"{base}/zotero/items?limit=10")["items"]
        assert items[0]["key"] == "PAPER1"
        result = _request_json(
            f"{base}/zotero/sync",
            {"item_key": "PAPER1", "max_chars": 2000},
        )
        assert result["ok"] is True
        assert store.get_context("zotero:PAPER1") is not None
        assert activated[-1].book_id == "zotero:PAPER1"
        assert activated[-1].kind == "zotero"
    finally:
        server.stop()


def test_zotero_context_is_not_spoiler_filtered(tmp_path: Path) -> None:
    memory = MemoryStore.open(tmp_path / "memory")
    reading = ReadingSessionStore.open(tmp_path / "reading")
    context = ReadingContext(
        book_id="zotero:PAPER1",
        kind="zotero",
        current_chapter="A Paper",
        cursor=0,
        spoiler_cursor=0,
    )
    chunk = ReadingChunk("zotero-PAPER1-000", "A Paper", 0, 100, "The full indexed paper text")
    reading.save_context(context)
    reading.save_chunks("zotero:PAPER1", [chunk])

    block = CompanionContextBuilder(MemoryContextProvider(memory), reading).build(
        "paper",
        book_id="zotero:PAPER1",
        chunks=[chunk],
    )
    assert "The full indexed paper text" in block
