"""JSONL-backed long-term memory with a rebuildable SQLite FTS5 index."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Callable, Iterable, Iterator, Mapping, Sequence

from .markdown_document import (
    DURABLE_MEMORY_KINDS,
    MarkdownMemoryEntry,
    document_hash,
    has_metadata_ids,
    parse_document,
    render_document,
)
from .models import MemoryRecord, MemoryStatus, normalize_iso, normalize_text, utc_now_iso


logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    namespace TEXT NOT NULL,
    tags TEXT NOT NULL,
    importance INTEGER NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    status TEXT NOT NULL,
    supersedes TEXT NOT NULL,
    superseded_by TEXT
);
CREATE INDEX IF NOT EXISTS memory_scope_namespace_idx
    ON memory(scope, namespace, status, valid_from, valid_to);
CREATE INDEX IF NOT EXISTS memory_hash_idx
    ON memory(content_hash, scope, namespace, kind, status);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    id UNINDEXED,
    text,
    tags,
    namespace,
    tokenize='trigram'
);
"""

_FTS_QUERY_SPLIT = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_PROJECTION_NAMESPACE_MARKER = re.compile(
    r"^\s*<!--\s*memory-namespace:\s*(?P<namespace>[^>]+?)\s*-->\s*$",
    re.MULTILINE,
)


def _fts_terms(query: str) -> list[str]:
    return [
        token
        for token in _FTS_QUERY_SPLIT.split(query)
        if token and token.isascii() and len(token) >= 3
    ]


def _like_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in _FTS_QUERY_SPLIT.split(query):
        if not token:
            continue
        if token.isascii():
            if len(token) >= 2:
                terms.append(token)
            continue
        for run in _CJK_RUN.findall(token):
            if len(run) <= 2:
                terms.append(run)
                continue
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(terms))[:24]


class MemoryStore:
    """Owns memory persistence and indexing; callers own semantic extraction."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.jsonl_path = self.root / "memory.jsonl"
        self.sqlite_path = self.root / "memory.sqlite3"
        self.markdown_path = self.root / "companion.md"
        self.markdown_hash_path = self.root / "companion.md.sha256"
        self.views_dir = self.root / "views"
        self.characters_dir = self.root / "characters"
        self._write_lock = threading.RLock()
        self._projection_lock = threading.RLock()
        self._syncing_projection = False

    @classmethod
    def open(cls, root: str | Path) -> "MemoryStore":
        store = cls(root)
        store.initialize()
        return store

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.views_dir.mkdir(parents=True, exist_ok=True)
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.touch(exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
        if self._index_count() == 0 and self.jsonl_path.stat().st_size:
            self.rebuild_index()
        self._refresh_projections_safely()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _index_count(self) -> int:
        if not self.sqlite_path.exists():
            return 0
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM memory").fetchone()
        return int(row["count"])

    def _record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            text=row["text"],
            kind=row["kind"],
            scope=row["scope"],
            namespace=row["namespace"],
            tags=tuple(json.loads(row["tags"])),
            importance=int(row["importance"]),
            confidence=float(row["confidence"]),
            source=row["source"],
            source_ids=tuple(json.loads(row["source_ids"])),
            content_hash=row["content_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            status=row["status"],
            supersedes=tuple(json.loads(row["supersedes"])),
            superseded_by=row["superseded_by"],
        )

    def _upsert(self, connection: sqlite3.Connection, record: MemoryRecord) -> None:
        payload = record.to_dict()
        connection.execute(
            """
            INSERT INTO memory (
                id, text, kind, scope, namespace, tags, importance, confidence,
                source, source_ids, content_hash, created_at, updated_at,
                valid_from, valid_to, status, supersedes, superseded_by
            ) VALUES (
                :id, :text, :kind, :scope, :namespace, :tags, :importance, :confidence,
                :source, :source_ids, :content_hash, :created_at, :updated_at,
                :valid_from, :valid_to, :status, :supersedes, :superseded_by
            )
            ON CONFLICT(id) DO UPDATE SET
                text=excluded.text,
                kind=excluded.kind,
                scope=excluded.scope,
                namespace=excluded.namespace,
                tags=excluded.tags,
                importance=excluded.importance,
                confidence=excluded.confidence,
                source=excluded.source,
                source_ids=excluded.source_ids,
                content_hash=excluded.content_hash,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                valid_from=excluded.valid_from,
                valid_to=excluded.valid_to,
                status=excluded.status,
                supersedes=excluded.supersedes,
                superseded_by=excluded.superseded_by
            """,
            {
                **payload,
                "tags": json.dumps(payload["tags"], ensure_ascii=False),
                "source_ids": json.dumps(payload["source_ids"], ensure_ascii=False),
                "supersedes": json.dumps(payload["supersedes"], ensure_ascii=False),
            },
        )
        connection.execute("DELETE FROM memory_fts WHERE id = ?", (record.id,))
        connection.execute(
            "INSERT INTO memory_fts(id, text, tags, namespace) VALUES (?, ?, ?, ?)",
            (record.id, record.text, " ".join(record.tags), record.namespace),
        )

    def _append_snapshots(self, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read_snapshots(self) -> dict[str, MemoryRecord]:
        latest: dict[str, MemoryRecord] = {}
        with self.jsonl_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    record = MemoryRecord.from_mapping(payload)
                except Exception as error:
                    raise ValueError(f"invalid memory snapshot at line {line_number}: {error}") from error
                latest[record.id] = record
        return latest

    def get(self, memory_id: str) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()
        return self._record_from_row(row) if row else None

    def remember(
        self,
        candidates: Iterable[MemoryRecord | Mapping[str, object]],
        *,
        now: str | None = None,
    ) -> list[MemoryRecord]:
        timestamp = normalize_iso(now or utc_now_iso())
        normalized: list[MemoryRecord] = []
        for candidate in candidates:
            record = candidate if isinstance(candidate, MemoryRecord) else MemoryRecord.from_mapping(candidate)
            normalized.append(record.evolve(updated_at=timestamp))
        accepted: list[MemoryRecord] = []
        with self._write_lock, self._connect() as connection:
            for record in normalized:
                duplicate = connection.execute(
                    """
                    SELECT id FROM memory
                    WHERE content_hash=? AND scope=? AND namespace=? AND kind=? AND status='active'
                    LIMIT 1
                    """,
                    (record.content_hash, record.scope, record.namespace, record.kind),
                ).fetchone()
                if duplicate:
                    continue
                accepted.append(record)
            if accepted:
                self._append_snapshots(accepted)
                connection.execute("BEGIN")
                try:
                    for record in accepted:
                        self._upsert(connection, record)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        if accepted:
            self._refresh_projections_safely()
        return accepted

    def recall(
        self,
        query: str,
        *,
        scopes: Sequence[str] | None = None,
        namespaces: Sequence[str] | None = None,
        limit: int = 5,
        as_of: str | None = None,
    ) -> list[MemoryRecord]:
        clean_query = normalize_text(query)
        clean_limit = max(1, min(int(limit), 50))
        effective_time = normalize_iso(as_of or utc_now_iso())
        common_where = [
            "m.valid_from <= ?",
            "(m.valid_to IS NULL OR m.valid_to > ?)",
        ]
        common_params: list[object] = [effective_time, effective_time]
        if scopes:
            placeholders = ",".join("?" for _ in scopes)
            common_where.append(f"m.scope IN ({placeholders})")
            common_params.extend(scopes)
        if namespaces:
            placeholders = ",".join("?" for _ in namespaces)
            common_where.append(f"m.namespace IN ({placeholders})")
            common_params.extend(namespaces)

        rows: list[sqlite3.Row] = []
        fts_terms = _fts_terms(clean_query)
        if fts_terms:
            fts_query = " OR ".join(
                f'"{term.replace(chr(34), chr(34) * 2)}"' for term in fts_terms
            )
            sql = (
                "SELECT m.*, bm25(memory_fts) AS score "
                "FROM memory_fts f JOIN memory m ON m.id = f.id "
                f"WHERE memory_fts MATCH ? AND {' AND '.join(common_where)} "
                "ORDER BY score ASC, m.importance DESC, m.updated_at DESC LIMIT ?"
            )
            try:
                with self._connect() as connection:
                    rows.extend(
                        connection.execute(
                            sql, [fts_query, *common_params, clean_limit * 3]
                        ).fetchall()
                    )
            except sqlite3.OperationalError:
                logger.debug("FTS memory recall unavailable; using LIKE fallback", exc_info=True)

        like_terms = _like_terms(clean_query)
        if like_terms:
            clauses: list[str] = []
            like_params: list[object] = [*common_params]
            for term in like_terms:
                clauses.append("(m.text LIKE ? OR m.tags LIKE ? OR m.namespace LIKE ?)")
                pattern = f"%{term}%"
                like_params.extend((pattern, pattern, pattern))
            sql = (
                "SELECT m.* FROM memory m "
                f"WHERE {' AND '.join(common_where)} AND ({' OR '.join(clauses)}) "
                "ORDER BY m.importance DESC, m.updated_at DESC LIMIT ?"
            )
            with self._connect() as connection:
                rows.extend(
                    connection.execute(sql, [*like_params, clean_limit * 3]).fetchall()
                )

        if not clean_query:
            sql = (
                "SELECT m.* FROM memory m "
                f"WHERE {' AND '.join(common_where)} "
                "ORDER BY m.importance DESC, m.updated_at DESC LIMIT ?"
            )
            with self._connect() as connection:
                rows.extend(connection.execute(sql, [*common_params, clean_limit]).fetchall())

        deduped: list[MemoryRecord] = []
        seen: set[str] = set()
        for row in rows:
            record = self._record_from_row(row)
            if record.id in seen:
                continue
            seen.add(record.id)
            deduped.append(record)
            if len(deduped) >= clean_limit:
                break
        return deduped

    def _fallback_like(
        self,
        query: str,
        *,
        scopes: Sequence[str] | None,
        namespaces: Sequence[str] | None,
        limit: int,
        as_of: str,
    ) -> list[sqlite3.Row]:
        where = [
            "status='active'",
            "valid_from <= ?",
            "(valid_to IS NULL OR valid_to > ?)",
            "(text LIKE ? OR tags LIKE ?)",
        ]
        params: list[object] = [as_of, as_of, f"%{query}%", f"%{query}%"]
        if scopes:
            where.append(f"scope IN ({','.join('?' for _ in scopes)})")
            params.extend(scopes)
        if namespaces:
            where.append(f"namespace IN ({','.join('?' for _ in namespaces)})")
            params.extend(namespaces)
        params.append(limit)
        with self._connect() as connection:
            return connection.execute(
                f"SELECT * FROM memory WHERE {' AND '.join(where)} "
                "ORDER BY importance DESC, updated_at DESC LIMIT ?",
                params,
            ).fetchall()

    def supersede(
        self,
        old_id: str,
        replacement: MemoryRecord | Mapping[str, object],
        *,
        now: str | None = None,
    ) -> MemoryRecord:
        timestamp = normalize_iso(now or utc_now_iso())
        with self._write_lock:
            old = self.get(old_id)
            if old is None:
                raise KeyError(f"unknown memory id: {old_id}")
            candidate = replacement if isinstance(replacement, MemoryRecord) else MemoryRecord.from_mapping(replacement)
            new_record = candidate.evolve(
                supersedes=tuple(dict.fromkeys((*candidate.supersedes, old.id))),
                valid_from=timestamp,
                updated_at=timestamp,
            )
            old_record = old.evolve(
                valid_to=timestamp,
                status=MemoryStatus.SUPERSEDED.value,
                superseded_by=new_record.id,
                updated_at=timestamp,
            )
            snapshots = (old_record, new_record)
            self._append_snapshots(snapshots)
            with self._connect() as connection:
                connection.execute("BEGIN")
                try:
                    self._upsert(connection, old_record)
                    self._upsert(connection, new_record)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        self._refresh_projections_safely()
        return new_record

    def revoke(self, memory_id: str, *, now: str | None = None) -> MemoryRecord:
        timestamp = normalize_iso(now or utc_now_iso())
        with self._write_lock:
            record = self.get(memory_id)
            if record is None:
                raise KeyError(f"unknown memory id: {memory_id}")
            revoked = record.evolve(
                status=MemoryStatus.REVOKED.value,
                valid_to=timestamp,
                updated_at=timestamp,
            )
            self._append_snapshots((revoked,))
            with self._connect() as connection:
                self._upsert(connection, revoked)
                connection.commit()
        self._refresh_projections_safely()
        return revoked

    def compact_namespace(
        self,
        namespace: str,
        *,
        scope: str | None = None,
        min_records: int = 10,
        summarizer: Callable[[Sequence[MemoryRecord]], str] | None = None,
        now: str | None = None,
    ) -> MemoryRecord | None:
        timestamp = normalize_iso(now or utc_now_iso())
        candidates = self.list_records(scopes=[scope] if scope else None, namespaces=[namespace], active_only=True)
        candidates = [record for record in candidates if record.kind != "summary"]
        if len(candidates) < max(2, int(min_records)):
            return None
        chosen_scope = scope or candidates[0].scope
        if summarizer:
            summary_text = normalize_text(summarizer(candidates))
        else:
            unique = list(dict.fromkeys(record.text for record in candidates))
            summary_text = normalize_text("；".join(unique[:30]))
        if not summary_text:
            return None
        tags = tuple(dict.fromkeys(tag for record in candidates for tag in record.tags))
        source_ids = tuple(record.id for record in candidates)
        summary = MemoryRecord.create(
            text=summary_text,
            kind="summary",
            scope=chosen_scope,
            namespace=namespace,
            tags=tags,
            importance=max(record.importance for record in candidates),
            confidence=min(record.confidence for record in candidates),
            source="compaction",
            source_ids=source_ids,
            created_at=timestamp,
        ).evolve(supersedes=source_ids)
        compacted = tuple(
            record.evolve(
                status=MemoryStatus.COMPACTED.value,
                valid_to=timestamp,
                superseded_by=summary.id,
                updated_at=timestamp,
            )
            for record in candidates
        )
        snapshots = (*compacted, summary)
        with self._write_lock:
            self._append_snapshots(snapshots)
            with self._connect() as connection:
                connection.execute("BEGIN")
                try:
                    for record in snapshots:
                        self._upsert(connection, record)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        self._refresh_projections_safely()
        return summary

    def list_records(
        self,
        *,
        scopes: Sequence[str] | None = None,
        namespaces: Sequence[str] | None = None,
        active_only: bool = False,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        where: list[str] = []
        params: list[object] = []
        if active_only:
            where.append("status='active'")
        if scopes:
            where.append(f"scope IN ({','.join('?' for _ in scopes)})")
            params.extend(scopes)
        if namespaces:
            where.append(f"namespace IN ({','.join('?' for _ in namespaces)})")
            params.extend(namespaces)
        sql = "SELECT * FROM memory"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def rebuild_index(self) -> int:
        records = self._read_snapshots()
        with self._write_lock, self._connect() as connection:
            connection.execute("DELETE FROM memory_fts")
            connection.execute("DELETE FROM memory")
            for record in records.values():
                self._upsert(connection, record)
            connection.commit()
        return len(records)

    def _refresh_projections_safely(self) -> None:
        if self._syncing_projection:
            return
        try:
            self.write_projections()
        except Exception:
            logger.warning("failed to refresh memory markdown projections", exc_info=True)

    @staticmethod
    def _write_text_atomic(path: Path, value: str) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(value, encoding="utf-8", newline="\n")
        temporary.replace(path)

    @staticmethod
    def _has_pending_manual_edits(path: Path, hash_path: Path) -> bool:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
        if not hash_path.is_file():
            # A legacy/manual document without a known baseline is only
            # imported when it carries stable IDs. Otherwise regenerating it
            # is safer than treating every bullet as a full historical delete.
            return has_metadata_ids(text)
        saved_hash = hash_path.read_text(encoding="utf-8").strip()
        return bool(saved_hash) and document_hash(text) != saved_hash

    @staticmethod
    def _read_markdown_entries(
        path: Path, *, default_namespace: str
    ) -> list[MarkdownMemoryEntry]:
        text = path.read_text(encoding="utf-8")
        entries = parse_document(text, default_namespace=default_namespace)
        has_bullets = any(
            line.lstrip().startswith(("- ", "* ")) for line in text.splitlines()
        )
        if has_bullets and not entries:
            raise ValueError("editable memory document contains bullets but none were parsed")
        return entries

    @staticmethod
    def _markdown_entry_matches(record: MemoryRecord, entry: MarkdownMemoryEntry) -> bool:
        return (
            record.text == entry.text
            and record.kind == entry.kind
            and record.scope == entry.scope
            and record.namespace == entry.namespace
            and int(record.importance) == int(entry.importance)
            and abs(float(record.confidence) - float(entry.confidence)) < 1e-9
            and tuple(record.tags) == tuple(entry.tags)
        )

    def _apply_markdown_entries(
        self,
        entries: Sequence[MarkdownMemoryEntry],
        *,
        namespace_filter: str,
    ) -> None:
        duplicate_ids = [
            memory_id
            for memory_id, count in _duplicate_counts(
                entry.memory_id for entry in entries if entry.memory_id
            ).items()
            if count > 1
        ]
        if duplicate_ids:
            raise ValueError(f"duplicate memory ids in editable document: {duplicate_ids}")

        existing = {
            record.id: record
            for record in self.list_records(active_only=True)
            if self._record_in_projection_namespace(record, namespace_filter)
            and record.kind in DURABLE_MEMORY_KINDS
        }
        present_ids = {entry.memory_id for entry in entries if entry.memory_id}
        self._syncing_projection = True
        try:
            for memory_id in sorted(set(existing) - present_ids):
                self.revoke(memory_id)
            for entry in entries:
                if not entry.text:
                    continue
                if entry.memory_id and entry.memory_id in existing:
                    current = existing[entry.memory_id]
                    if self._markdown_entry_matches(current, entry):
                        continue
                    updated = current.evolve(
                        text=entry.text,
                        kind=entry.kind,
                        scope=entry.scope,
                        namespace=entry.namespace,
                        tags=entry.tags,
                        importance=entry.importance,
                        confidence=entry.confidence,
                        source="manual_markdown",
                        content_hash="",
                        updated_at=utc_now_iso(),
                        valid_to=None,
                        status=MemoryStatus.ACTIVE.value,
                        superseded_by=None,
                    )
                    self.update_manual(updated)
                else:
                    self.remember(
                        [
                            MemoryRecord.create(
                                memory_id=entry.memory_id,
                                text=entry.text,
                                kind=entry.kind,
                                scope=entry.scope,
                                namespace=entry.namespace,
                                tags=entry.tags,
                                importance=entry.importance,
                                confidence=entry.confidence,
                                source="manual_markdown",
                            )
                        ]
                    )
        finally:
            self._syncing_projection = False

    def update_manual(self, record: MemoryRecord) -> MemoryRecord:
        """Persist an in-place edit while keeping prior JSONL snapshots."""
        with self._write_lock, self._connect() as connection:
            self._append_snapshots((record,))
            connection.execute("BEGIN")
            try:
                self._upsert(connection, record)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._refresh_projections_safely()
        return record

    def update_namespaces(
        self,
        namespaces: Mapping[str, str],
        *,
        source: str = "namespace_migration",
    ) -> list[MemoryRecord]:
        """Bulk-move records between namespaces while retaining history."""
        cleaned = {
            str(memory_id): normalize_text(namespace)
            for memory_id, namespace in namespaces.items()
            if str(memory_id) and normalize_text(namespace)
        }
        if not cleaned:
            return []
        timestamp = utc_now_iso()
        updated: list[MemoryRecord] = []
        with self._write_lock, self._connect() as connection:
            for memory_id, namespace in cleaned.items():
                row = connection.execute(
                    "SELECT * FROM memory WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    continue
                current = self._record_from_row(row)
                if current.namespace == namespace:
                    continue
                updated.append(
                    current.evolve(
                        namespace=namespace,
                        content_hash="",
                        updated_at=timestamp,
                        source=source,
                    )
                )
            if updated:
                self._append_snapshots(updated)
                connection.execute("BEGIN")
                try:
                    for record in updated:
                        self._upsert(connection, record)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        if updated:
            self._refresh_projections_safely()
        return updated

    def update_texts(
        self,
        translations: Mapping[str, str],
        *,
        source: str = "translation",
    ) -> list[MemoryRecord]:
        """Bulk-rewrite record text while retaining JSONL history snapshots."""
        cleaned = {
            str(memory_id): normalize_text(value)
            for memory_id, value in translations.items()
            if str(memory_id) and normalize_text(value)
        }
        if not cleaned:
            return []
        timestamp = utc_now_iso()
        updated: list[MemoryRecord] = []
        with self._write_lock, self._connect() as connection:
            for memory_id, value in cleaned.items():
                row = connection.execute(
                    "SELECT * FROM memory WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    continue
                current = self._record_from_row(row)
                if current.text == value:
                    continue
                updated.append(
                    current.evolve(
                        text=value,
                        content_hash="",
                        updated_at=timestamp,
                        source=source,
                    )
                )
            if updated:
                self._append_snapshots(updated)
                connection.execute("BEGIN")
                try:
                    for record in updated:
                        self._upsert(connection, record)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        if updated:
            self._refresh_projections_safely()
        return updated

    @staticmethod
    def _record_in_projection_namespace(record: MemoryRecord, namespace: str) -> bool:
        if namespace == "general":
            return not record.namespace.startswith("character:")
        return record.namespace == namespace

    @staticmethod
    def _safe_namespace_filename(namespace: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", namespace).strip("-") or "general"

    def _character_namespaces(self, records: Sequence[MemoryRecord]) -> list[str]:
        namespaces = {
            record.namespace
            for record in records
            if record.namespace.startswith("character:")
        }
        if self.characters_dir.is_dir():
            for path in sorted(self.characters_dir.glob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                match = _PROJECTION_NAMESPACE_MARKER.search(text)
                if match:
                    namespace = match.group("namespace").strip()
                elif path.stem.startswith("character-"):
                    namespace = "character:" + path.stem[len("character-") :]
                else:
                    continue
                if namespace.startswith("character:"):
                    namespaces.add(namespace)
        return sorted(namespaces)

    def _editable_projection_specs(
        self, records: Sequence[MemoryRecord]
    ) -> list[tuple[str, Path, Path, str]]:
        specs = [
            ("general", self.markdown_path, self.markdown_hash_path, "共享记忆（用户/阅读/研究）"),
        ]
        for namespace in self._character_namespaces(records):
            safe_name = self._safe_namespace_filename(namespace)
            path = self.characters_dir / f"{safe_name}.md"
            specs.append(
                (
                    namespace,
                    path,
                    path.with_name(path.name + ".sha256"),
                    f"角色记忆: {namespace}",
                )
            )
        return specs

    def write_projections(self, *, force: bool = False) -> None:
        """Sync editable documents, then render global, character and view files."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.views_dir.mkdir(parents=True, exist_ok=True)
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        with self._projection_lock:
            records = self.list_records(active_only=True)
            for namespace, path, hash_path, _title in self._editable_projection_specs(records):
                if force or not self._has_pending_manual_edits(path, hash_path):
                    continue
                try:
                    self._apply_markdown_entries(
                        self._read_markdown_entries(
                            path, default_namespace=namespace
                        ),
                        namespace_filter=namespace,
                    )
                except Exception:
                    logger.warning(
                        "manual memory document %s could not be imported; preserving it",
                        path,
                        exc_info=True,
                    )
                    return

            records = self.list_records(active_only=True)
            specs = self._editable_projection_specs(records)
            for namespace, path, hash_path, title in specs:
                durable_records = [
                    record
                    for record in records
                    if self._record_in_projection_namespace(record, namespace)
                    and record.kind in DURABLE_MEMORY_KINDS
                ]
                document = render_document(
                    durable_records,
                    title=title,
                    editable=True,
                )
                if namespace != "general":
                    document = (
                        f"<!-- memory-namespace: {namespace} -->\n" + document
                    )
                self._write_text_atomic(path, document)
                self._write_text_atomic(hash_path, document_hash(document) + "\n")

            by_namespace: dict[str, list[MemoryRecord]] = {}
            for record in records:
                by_namespace.setdefault(record.namespace, []).append(record)
            view_lines = [
                "# Memory Views",
                "",
                "> 可编辑文档按全局和角色分开管理；下面还有自动生成的完整 namespace 视图。",
                "",
                "## Editable documents",
                "",
            ]
            for namespace, path, _hash_path, title in specs:
                relative = os.path.relpath(path, self.views_dir).replace(os.sep, "/")
                count = len(
                    [
                        record
                        for record in records
                        if self._record_in_projection_namespace(record, namespace)
                        and record.kind in DURABLE_MEMORY_KINDS
                    ]
                )
                view_lines.append(f"- [{title}]({relative}) - {count} editable")

            view_lines.extend(["", "## Generated namespace views", ""])
            for namespace in sorted(by_namespace):
                namespace_records = by_namespace[namespace]
                safe_name = self._safe_namespace_filename(namespace)
                filename = f"{safe_name}.md"
                self._write_text_atomic(
                    self.views_dir / filename,
                    render_document(
                        namespace_records,
                        title=f"Memory: {namespace}",
                        editable=False,
                    ),
                )
                view_lines.append(
                    f"- [{namespace}]({filename}) - {len(namespace_records)} active"
                )
            if not by_namespace:
                view_lines.append("_No active namespace memories._")
            self._write_text_atomic(
                self.views_dir / "index.md",
                "\n".join(view_lines).rstrip() + "\n",
            )


def _duplicate_counts(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
