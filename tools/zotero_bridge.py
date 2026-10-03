"""CLI for the Zotero 7 local API and Amadeus Reading Bridge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.asset_paths import PROJECT_ROOT  # noqa: E402
from core.reading import ReadingSessionStore, ZoteroLocalClient, ZoteroUnavailable  # noqa: E402


def _client(args: argparse.Namespace) -> ZoteroLocalClient:
    return ZoteroLocalClient(base_url=args.base_url, timeout=args.timeout)


def _store(args: argparse.Namespace) -> ReadingSessionStore:
    root = Path(args.store_dir).expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return ReadingSessionStore.open(root)


def _print_item(item: dict) -> None:
    authors = ", ".join(item.get("creators") or []) or "Unknown author"
    print(f"[{item.get('key', '')}] {item.get('title') or '(untitled)'}")
    print(f"  {authors} · {item.get('date') or 'no date'} · {item.get('itemType') or 'item'}")
    if item.get("DOI"):
        print(f"  DOI: {item['DOI']}")


def command_status(args: argparse.Namespace) -> int:
    result = _client(args).status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("available") else 2


def command_list(args: argparse.Namespace) -> int:
    items = _client(args).list_items(
        query=args.query,
        item_type=args.item_type,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps({"ok": True, "items": items}, ensure_ascii=False, indent=2))
    elif not items:
        print("No Zotero items found.")
    else:
        for item in items:
            _print_item(item)
    return 0


def command_sync(args: argparse.Namespace) -> int:
    result = _client(args).sync_item(
        _store(args),
        args.item_key,
        selected_text=args.text,
        max_chars=args.max_chars,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:23119/api/users/0")
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--store-dir", default="runtime/companion/reading")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Check whether Zotero local API is reachable")
    status.set_defaults(func=command_status)

    listing = sub.add_parser("list", help="List or search top-level Zotero items")
    listing.add_argument("--query", default="")
    listing.add_argument("--item-type", default="")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=command_list)

    search = sub.add_parser("search", help="Alias for list --query")
    search.add_argument("query")
    search.add_argument("--item-type", default="")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_list)

    sync = sub.add_parser("sync", help="Sync one Zotero item into the reading session store")
    sync.add_argument("item_key")
    sync.add_argument("--text", default="", help="Use this selected text instead of indexed full text")
    sync.add_argument("--max-chars", type=int, default=40000)
    sync.set_defaults(func=command_sync)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args) or 0)
    except ZoteroUnavailable as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
