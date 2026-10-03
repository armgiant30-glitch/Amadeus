"""Package the Amadeus Zotero Bridge as an installable .xpi."""

from __future__ import annotations

import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "zotero-plugin" / "amadeus-zotero-bridge"


def package(output: Path) -> Path:
    if not (SOURCE / "manifest.json").is_file():
        raise FileNotFoundError(f"Zotero plugin source not found: {SOURCE}")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in ("manifest.json", "bootstrap.js", "icon-48.png", "icon-96.png"):
            path = SOURCE / rel
            if path.is_file():
                archive.write(path, rel)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release" / "amadeus-zotero-bridge.xpi",
    )
    args = parser.parse_args()
    print(package(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
