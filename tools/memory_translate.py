"""Translate active long-term memory entries into Simplified Chinese.

This is an explicit migration tool. It uses the configured remote LLM, backs up
memory.jsonl before writing, and only applies translations after every batch
has parsed successfully.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys

from core.memory import MemoryStore
from core.memory.host_extractor import extract_json_payload


_CJK = re.compile(r"[\u3400-\u9fff]")
_ASCII_ALPHA = re.compile(r"[A-Za-z]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z'+-]*")
_ENGLISH_RUN = re.compile(r"\b(?:[A-Za-z][A-Za-z'+-]*\s+){3,}[A-Za-z][A-Za-z'+-]*\b")
_ENGLISH_FUNCTION = re.compile(
    r"\b(?:the|is|are|was|were|user|assistant|has|have|with|and|or|for|from|in|on|to|by|of|that|this|currently|reading|interested|working|prefers|wants)\b",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "You translate long-term memory entries into natural, concise Simplified Chinese. "
    "Preserve names, work titles, acronyms, technical terms, file paths, URLs, numbers, "
    "and code identifiers. Do not merge or split entries. Do not add facts. "
    'Return JSON only: {"translations":[{"id":"...","text":"..."}]}'
)


def needs_translation(text: str) -> bool:
    value = str(text or "")
    cjk = len(_CJK.findall(value))
    ascii_alpha = len(_ASCII_ALPHA.findall(value))
    ascii_words = _ASCII_WORD.findall(value)
    function_hits = {
        match.group(0).lower() for match in _ENGLISH_FUNCTION.finditer(value)
    }
    return (
        ascii_alpha > (cjk * 2 + 8)
        or len(function_hits) >= 4
        or (cjk == 0 and len(ascii_words) >= 4)
    )


def translate_batch(
    entries: list[dict[str, str]],
    *,
    client,
    model: str,
) -> dict[str, str]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"entries": entries}, ensure_ascii=False),
            },
        ],
        temperature=0.1,
        max_tokens=4096,
        stream=False,
        timeout=60.0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    raw = str(response.choices[0].message.content or "")
    payload = extract_json_payload(raw)
    items = payload.get("translations") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("translator returned no translations list")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("id") or "").strip()
        text = str(item.get("text") or "").strip()
        if memory_id and text:
            result[memory_id] = text
    missing = [entry["id"] for entry in entries if entry["id"] not in result]
    if missing:
        raise ValueError(f"translator omitted ids: {missing}")
    return result


def _client_and_model(model_override: str):
    from config import settings
    from openai import OpenAI

    if getattr(settings, "DEEPSEEK_API_KEY", ""):
        return (
            OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
            ),
            model_override or settings.DEEPSEEK_MODEL_NAME,
        )
    if getattr(settings, "OPENAI_API_KEY", ""):
        return (
            OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL),
            model_override or getattr(settings, "OPENAI_MODEL_NAME", "gpt-5.4-mini"),
        )
    raise RuntimeError("no translation LLM API key is configured")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = MemoryStore.open(args.root)
    records = [
        record
        for record in store.list_records(active_only=True)
        if record.kind != "summary" and needs_translation(record.text)
    ]
    if args.limit > 0:
        records = records[: args.limit]
    print(
        json.dumps(
            {
                "root": str(store.root),
                "active_records": len(store.list_records(active_only=True)),
                "translate_candidates": len(records),
                "preview": [
                    {"id": record.id, "text": record.text[:120]} for record in records[:5]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.dry_run or not records:
        return

    client, model = _client_and_model(args.model)
    batch_size = max(1, min(int(args.batch_size), 20))
    translations: dict[str, str] = {}
    for index in range(0, len(records), batch_size):
        batch = records[index : index + batch_size]
        entries = [{"id": record.id, "text": record.text} for record in batch]
        translations.update(
            translate_batch(entries, client=client, model=model)
        )
        print(
            f"translated {min(index + batch_size, len(records))}/{len(records)}",
            file=sys.stderr,
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = store.jsonl_path.with_name(
        f"{store.jsonl_path.name}.bak-{timestamp}"
    )
    shutil.copy2(store.jsonl_path, backup)
    updated = store.update_texts(translations, source="translation")
    store.write_projections()
    print(
        json.dumps(
            {
                "backup": str(backup),
                "translated": len(updated),
                "model": model,
                "sample": [
                    {"id": record.id, "text": record.text}
                    for record in updated[:5]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
