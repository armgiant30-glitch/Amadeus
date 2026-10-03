"""Synchronize and export human-readable long-term memory documents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from core.memory import MemoryStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate from JSONL without importing pending manual edits.",
    )
    args = parser.parse_args()

    store = MemoryStore.open(args.root)
    store.write_projections(force=args.force)
    active = store.list_records(active_only=True)
    result = {
        "root": str(store.root),
        "companion": str(store.markdown_path),
        "hash": str(store.markdown_hash_path),
        "views_dir": str(store.views_dir),
        "active_records": len(active),
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
