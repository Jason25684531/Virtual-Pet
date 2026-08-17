from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def restore(character_id: str, timestamp: str | None = None, project_root: Path | None = None) -> Path:
    root = (project_root or Path.cwd()).resolve()
    saves = root / "data" / "saves" / character_id
    snapshot = saves / timestamp if timestamp else max((path for path in saves.iterdir() if path.is_dir() and not path.name.startswith(".")), key=lambda path: path.name)
    if not snapshot.is_dir():
        raise FileNotFoundError(snapshot)
    character = root / "data" / "characters" / character_id
    if (character / "qdrant" / ".lock").exists():
        raise RuntimeError("qdrant is locked; close the app before restoring")
    character.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot / "state.db", character / "state.db")
    for path in (snapshot / "character").glob("*.json"):
        shutil.copy2(path, character / path.name)
    shutil.rmtree(character / "qdrant", ignore_errors=True)
    for path in (snapshot / "assets_manifests").rglob("manifest.json"):
        relative = path.relative_to(snapshot / "assets_manifests")
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("character_id")
    parser.add_argument("timestamp", nargs="?")
    args = parser.parse_args()
    try:
        print(restore(args.character_id, args.timestamp))
    except Exception as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
