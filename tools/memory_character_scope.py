"""Move existing character-specific memories into character namespaces.

The initial memory store used only the shared general namespace. This migration
keeps user-global memories in general and moves explicit assistant identity,
relationship, promise, and shared-experience records to character:<id>.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from core.memory import MemoryStore


def classify(record) -> str | None:
    if record.namespace != "general":
        return None
    text = record.text
    lowered = text.lower()

    if "牧" in text and any(
        marker in text or marker in lowered
        for marker in ("助手", "assistant", "自称", "自认", "identifies")
    ):
        return "character:kurisu"

    if "八千代" in text or "yachiyo" in lowered:
        # Keep the general music preference shared across characters.
        if "喜欢听歌" in text and "唱歌" in text:
            return None
        return "character:yachiyo"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = MemoryStore.open(args.root)
    active = store.list_records(active_only=True)
    updates = {
        record.id: classify(record)
        for record in active
        if classify(record) is not None
    }
    result = {
        "root": str(store.root),
        "active_records": len(active),
        "updates": [
            {
                "id": record.id,
                "from": record.namespace,
                "to": updates[record.id],
                "text": record.text,
            }
            for record in active
            if record.id in updates
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.dry_run or not updates:
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = store.jsonl_path.with_name(
        f"{store.jsonl_path.name}.bak-character-{timestamp}"
    )
    shutil.copy2(store.jsonl_path, backup)
    updated = store.update_namespaces(updates)
    store.write_projections()
    print(
        json.dumps(
            {
                "backup": str(backup),
                "updated": len(updated),
                "namespaces": {
                    namespace: len(store.list_records(namespaces=[namespace], active_only=True))
                    for namespace in sorted(set(updates.values()))
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
