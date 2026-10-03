"""Human-editable Markdown projection for durable long-term memory.

The Markdown document is not a second source of truth. It is a bidirectional
view: humans edit the text in place, and the store imports those edits into the
JSONL log before rendering a fresh document. Stable metadata comments preserve
record identity while keeping the visible document readable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
import html
import re

from .models import MemoryRecord, normalize_text


DURABLE_MEMORY_KINDS = frozenset({"preference", "constraint", "fact", "summary"})
DEFAULT_KIND = "fact"
DEFAULT_SCOPE = "user"
DEFAULT_NAMESPACE = "general"
DEFAULT_IMPORTANCE = 3
DEFAULT_CONFIDENCE = 0.8

_METADATA_RE = re.compile(r"^\s*<!--\s*memory\s+(?P<body>.*?)\s*-->\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
_HEADING_RE = re.compile(r"^\s*##+\s+(?P<kind>[A-Za-z0-9_.-]+)\s*$")
_ATTR_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"(?P<value>[^\"]*)\"")


@dataclass(frozen=True, slots=True)
class MarkdownMemoryEntry:
    """One editable bullet parsed from companion.md."""

    text: str
    kind: str = DEFAULT_KIND
    scope: str = DEFAULT_SCOPE
    namespace: str = DEFAULT_NAMESPACE
    importance: int = DEFAULT_IMPORTANCE
    confidence: float = DEFAULT_CONFIDENCE
    tags: tuple[str, ...] = ()
    memory_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", normalize_text(self.text))
        object.__setattr__(self, "kind", normalize_text(self.kind) or DEFAULT_KIND)
        object.__setattr__(self, "scope", normalize_text(self.scope) or DEFAULT_SCOPE)
        object.__setattr__(
            self, "namespace", normalize_text(self.namespace) or DEFAULT_NAMESPACE
        )
        object.__setattr__(self, "importance", max(1, min(int(self.importance), 5)))
        object.__setattr__(
            self, "confidence", max(0.0, min(float(self.confidence), 1.0))
        )
        object.__setattr__(
            self,
            "tags",
            tuple(
                dict.fromkeys(
                    normalize_text(tag) for tag in self.tags if normalize_text(tag)
                )
            ),
        )
        memory_id = normalize_text(self.memory_id) if self.memory_id else None
        object.__setattr__(self, "memory_id", memory_id)


def document_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def render_document(
    records: Iterable[MemoryRecord],
    *,
    title: str = "Companion Memory",
    editable: bool = True,
) -> str:
    """Render records as a readable Markdown document with stable metadata."""
    grouped: dict[str, list[MemoryRecord]] = {}
    for record in records:
        grouped.setdefault(record.kind, []).append(record)

    lines = [f"# {title}", ""]
    if editable:
        lines.extend(
            [
                "> 这份文档可以直接编辑：修改正文会更新记忆，新增条目会创建记忆，删除条目会撤销记忆。",
                "> 自动生成条目下方的 HTML 注释保存稳定 ID；请保留注释，否则该条目会被当成新记忆。",
                "> memory.jsonl 仍然是权威日志，删除 Markdown 条目不会物理删除历史。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "> 这是自动生成的场景视图，请编辑主文档 companion.md。",
                "",
            ]
        )

    for kind in sorted(grouped):
        lines.extend((f"## {kind}", ""))
        for record in sorted(
            grouped[kind], key=lambda item: (-item.importance, item.updated_at, item.text)
        ):
            lines.append(_metadata_comment(record))
            lines.append(f"- {record.text}")
            lines.append("")
    if not grouped:
        lines.extend(("_当前没有可显示的长期记忆。_", ""))
    return "\n".join(lines).rstrip() + "\n"


def parse_document(
    text: str,
    *,
    default_kind: str = DEFAULT_KIND,
    default_scope: str = DEFAULT_SCOPE,
    default_namespace: str = DEFAULT_NAMESPACE,
    default_importance: int = DEFAULT_IMPORTANCE,
    default_confidence: float = DEFAULT_CONFIDENCE,
) -> list[MarkdownMemoryEntry]:
    """Parse editable bullets and their nearest heading/metadata."""
    entries: list[MarkdownMemoryEntry] = []
    current_kind: str | None = None
    pending: dict[str, str] = {}
    for raw_line in str(text).splitlines():
        heading = _HEADING_RE.match(raw_line)
        if heading:
            current_kind = heading.group("kind")
            pending = {}
            continue
        metadata = _METADATA_RE.match(raw_line)
        if metadata:
            pending = _parse_metadata(metadata.group("body"))
            continue
        bullet = _BULLET_RE.match(raw_line)
        if not bullet:
            continue
        body = normalize_text(bullet.group("text"))
        if not body or body.startswith("<!--"):
            continue
        kind = normalize_text(current_kind or pending.get("kind")) or default_kind
        tags = _parse_tags(pending.get("tags", ""))
        entries.append(
            MarkdownMemoryEntry(
                text=body,
                kind=kind,
                scope=pending.get("scope") or default_scope,
                namespace=pending.get("namespace") or default_namespace,
                importance=_parse_int(pending.get("importance"), default_importance),
                confidence=_parse_float(
                    pending.get("confidence"), default_confidence
                ),
                tags=tags,
                memory_id=pending.get("id"),
            )
        )
        pending = {}
    return entries


def has_metadata_ids(text: str) -> bool:
    return "<!-- memory " in str(text) and "id=" in str(text)


def _metadata_comment(record: MemoryRecord) -> str:
    tags = ",".join(record.tags)
    return (
        "<!-- memory "
        f'id="{_escape_attr(record.id)}" '
        f'kind="{_escape_attr(record.kind)}" '
        f'scope="{_escape_attr(record.scope)}" '
        f'namespace="{_escape_attr(record.namespace)}" '
        f'importance="{int(record.importance)}" '
        f'confidence="{float(record.confidence):.4f}" '
        f'tags="{_escape_attr(tags)}" '
        "-->"
    )


def _parse_metadata(body: str) -> dict[str, str]:
    return {
        match.group("name"): html.unescape(match.group("value"))
        for match in _ATTR_RE.finditer(body)
    }


def _parse_tags(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalize_text(tag)
            for tag in str(value).replace(";", ",").split(",")
            if normalize_text(tag)
        )
    )


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _parse_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _escape_attr(value: object) -> str:
    return html.escape(str(value), quote=True).replace("\n", " ")
