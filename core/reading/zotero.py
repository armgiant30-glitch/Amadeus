"""Zotero 7 local-API adapter for the Reading Bridge."""

from __future__ import annotations

from collections.abc import Mapping
import html
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from .models import ReadingChunk, ReadingContext
from .session import ReadingSessionStore


class ZoteroError(RuntimeError):
    """Zotero API or response failure."""


class ZoteroUnavailable(ZoteroError):
    """Zotero is not running or its local API is disabled."""


def _unwrap_list(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, Mapping):
        for key in ("items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _unwrap_item(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ZoteroError("Zotero returned a non-object item")
    return dict(payload)


def _creator_name(creator: Mapping[str, Any]) -> str:
    name = str(creator.get("name") or "").strip()
    if name:
        return name
    first = str(creator.get("firstName") or "").strip()
    last = str(creator.get("lastName") or "").strip()
    return " ".join(part for part in (first, last) if part)


def _split_chunks(text: str, *, max_chars: int) -> list[tuple[int, int, str]]:
    if not text:
        return []
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(
                text.rfind("\n\n", start, end),
                text.rfind("。", start, end),
                text.rfind(". ", start, end),
            )
            if boundary > start + max_chars // 3:
                end = boundary + 1
        chunks.append((start, end, text[start:end]))
        start = end
    return chunks


class ZoteroLocalClient:
    """Read-only client for the Zotero 7 loopback HTTP API."""

    def __init__(self, *, base_url: str = "http://127.0.0.1:23119/api/users/0", timeout: float = 4.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = max(0.5, float(timeout))

    def _url(self, path: str, query: Mapping[str, object] | None = None) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        params = dict(query or {})
        params.setdefault("format", "json")
        return f"{self.base_url}{suffix}?{urlencode(params)}"

    def _request_json(self, path: str, query: Mapping[str, object] | None = None) -> object:
        request = Request(self._url(path, query), headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except Exception as error:
            raise ZoteroUnavailable(str(error)) from error
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise ZoteroError("Zotero returned invalid JSON") from error

    def status(self) -> dict[str, Any]:
        try:
            payload = self._request_json("/items", {"limit": 1})
        except ZoteroUnavailable as error:
            return {"ok": False, "available": False, "base_url": self.base_url, "error": str(error)}
        return {"ok": True, "available": True, "base_url": self.base_url, "returned": len(_unwrap_list(payload))}

    def list_items(self, *, query: str = "", item_type: str = "", limit: int = 50, start: int = 0) -> list[dict[str, Any]]:
        params: dict[str, object] = {
            "limit": max(1, min(int(limit), 100)),
            "start": max(0, int(start)),
            "sort": "dateModified",
            "direction": "desc",
        }
        if str(query or "").strip():
            params["q"] = str(query).strip()
        if str(item_type or "").strip():
            params["itemType"] = str(item_type).strip()
        payload = self._request_json("/items/top", params)
        return [self.summarize(item) for item in _unwrap_list(payload)]

    def get_item(self, item_key: str) -> dict[str, Any]:
        key = str(item_key or "").strip()
        if not key:
            raise ValueError("item_key is required")
        return _unwrap_item(self._request_json(f"/items/{key}"))

    def get_children(self, item_key: str) -> list[dict[str, Any]]:
        key = str(item_key or "").strip()
        if not key:
            raise ValueError("item_key is required")
        return _unwrap_list(self._request_json(f"/items/{key}/children"))

    def get_fulltext(self, attachment_key: str) -> str:
        key = str(attachment_key or "").strip()
        if not key:
            return ""
        try:
            payload = self._request_json(f"/items/{key}/fulltext")
        except ZoteroError:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, Mapping):
            for field in ("content", "text", "fulltext"):
                value = payload.get(field)
                if isinstance(value, str):
                    return value
        return ""

    @staticmethod
    def summarize(item: Mapping[str, Any]) -> dict[str, Any]:
        data = item.get("data") if isinstance(item.get("data"), Mapping) else item
        data = data if isinstance(data, Mapping) else {}
        creators = data.get("creators") if isinstance(data.get("creators"), list) else []
        author_list = [_creator_name(creator) for creator in creators if isinstance(creator, Mapping)]
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        return {
            "key": str(item.get("key") or data.get("key") or ""),
            "version": item.get("version"),
            "itemType": str(data.get("itemType") or ""),
            "title": str(data.get("title") or ""),
            "creators": [name for name in author_list if name],
            "date": str(data.get("date") or ""),
            "DOI": str(data.get("DOI") or ""),
            "url": str(data.get("url") or ""),
            "abstract": str(data.get("abstractNote") or ""),
            "tags": [str(tag.get("tag") or "") for tag in tags if isinstance(tag, Mapping) and tag.get("tag")],
            "dateModified": str(data.get("dateModified") or ""),
        }

    @staticmethod
    def _attachment_score(item: Mapping[str, Any]) -> int:
        data = item.get("data") if isinstance(item.get("data"), Mapping) else item
        data = data if isinstance(data, Mapping) else {}
        content_type = str(data.get("contentType") or "").lower()
        if "pdf" in content_type:
            return 3
        if "html" in content_type or "text" in content_type or content_type.startswith("text/"):
            return 2
        return 1

    @staticmethod
    def _item_data(item: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(item, Mapping):
            return {}
        data = item.get("data") if isinstance(item.get("data"), Mapping) else item
        return dict(data) if isinstance(data, Mapping) else {}

    @classmethod
    def _local_attachment_text(cls, item: Mapping[str, Any] | None) -> str:
        """Read a local Markdown/text attachment through Zotero's enclosure link."""
        if not isinstance(item, Mapping):
            return ""
        data = cls._item_data(item)
        content_type = str(data.get("contentType") or "").lower()
        filename = str(data.get("filename") or "")
        if not (
            content_type.startswith("text/")
            or content_type in {"application/json", "application/xml"}
            or filename.lower().endswith((".md", ".markdown", ".txt", ".html", ".htm", ".json", ".xml"))
        ):
            return ""
        links = item.get("links") if isinstance(item.get("links"), Mapping) else {}
        enclosure = links.get("enclosure") if isinstance(links.get("enclosure"), Mapping) else {}
        href = str(enclosure.get("href") or "").strip()
        if href.startswith("file://"):
            parsed = urlparse(href)
            path_text = unquote(parsed.path or "")
            if path_text.startswith("/") and re.match(r"^/[A-Za-z]:/", path_text):
                path_text = path_text[1:]
            path = Path(path_text)
        else:
            return ""
        if not path.is_file():
            return ""
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            return ""
        if content_type in {"text/html", "application/xhtml+xml"} or filename.lower().endswith((".html", ".htm")):
            text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
        return text.replace("\r\n", "\n").strip()

    def resolve_attachment(self, item_key: str) -> dict[str, Any] | None:
        children = self.get_children(item_key)
        attachments = []
        for child in children:
            data = child.get("data") if isinstance(child.get("data"), Mapping) else child
            data = data if isinstance(data, Mapping) else {}
            if str(data.get("itemType") or "") == "attachment":
                attachments.append(child)
        return max(attachments, key=self._attachment_score) if attachments else None

    def sync_item(
        self,
        store: ReadingSessionStore,
        item_key: str,
        *,
        selected_text: str = "",
        max_chars: int = 40000,
    ) -> dict[str, Any]:
        item = self.get_item(item_key)
        summary = self.summarize(item)
        key = summary["key"] or str(item_key)
        attachment = item if str(self._item_data(item).get("itemType") or "") == "attachment" else self.resolve_attachment(key)
        attachment_key = ""
        if attachment is not None:
            data = self._item_data(attachment)
            attachment_key = str(data.get("key") or attachment.get("key") or "")
        explicit = str(selected_text or "").strip()
        local_text = self._local_attachment_text(attachment)
        fulltext = explicit or local_text or self.get_fulltext(attachment_key)
        if not fulltext:
            fulltext = str(summary.get("abstract") or "").strip()
        if not fulltext:
            # Reuse the repository's paper-research path instead of inventing
            # a second PDF extractor. Zotero supplies bibliographic identity;
            # Qwen Web Research retrieves a grounded summary from the public
            # source when the local attachment has no indexed text.
            research_url = str(summary.get("url") or "").strip()
            if not research_url and summary.get("DOI"):
                research_url = f"https://doi.org/{summary['DOI']}"
            query = " ".join(
                part for part in (
                    "Retrieve and summarize this paper for reading context.",
                    f"Title: {summary.get('title') or key}.",
                    f"Authors: {', '.join(summary.get('creators') or [])}.",
                    f"URL: {research_url}." if research_url else "",
                ) if part
            )
            try:
                from llm.qwen_client import qwen_web_research

                researched = qwen_web_research(query, max_tokens=1200)
            except Exception:
                researched = None
            if isinstance(researched, Mapping):
                fulltext = str(researched.get("answer") or "").strip()
        if not fulltext:
            raise ZoteroError(
                f"Zotero item {key} has no selected text, indexed full text, abstract, or retrievable paper source"
            )

        source_chars = len(fulltext)
        truncated = False
        if not explicit:
            clean_limit = max(1000, min(int(max_chars), 200000))
            if len(fulltext) > clean_limit:
                fulltext = fulltext[:clean_limit]
                truncated = True
        chapter = summary["title"] or f"Zotero item {key}"
        book_id = f"zotero:{key}"
        pieces = _split_chunks(fulltext, max_chars=8000)
        chunks = [
            ReadingChunk(
                id=f"zotero-{key}-{index:03d}",
                chapter=chapter,
                start_offset=start,
                end_offset=end,
                page=None,
                text=piece,
            )
            for index, (start, end, piece) in enumerate(pieces)
        ]
        context = ReadingContext(
            book_id=book_id,
            kind="zotero",
            current_chapter=chapter,
            current_page=None,
            cursor=len(fulltext),
            spoiler_cursor=len(fulltext),
            selected_start=0,
            selected_end=len(fulltext),
        )
        store.save_chunks(book_id, chunks)
        store.save_context(context)
        return {
            "ok": True,
            "book_id": book_id,
            "item": summary,
            "attachment_key": attachment_key,
            "chars": len(fulltext),
            "source_chars": source_chars,
            "truncated": truncated,
            "chunks": len(chunks),
            "fulltext": bool(attachment_key and not explicit),
        }
