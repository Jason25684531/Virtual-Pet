"""Create the flat-layout scaffold for a manually imported character."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_ROOT = Path.cwd()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a manual character scaffold")
    parser.add_argument("character_id")
    parser.add_argument("display_name")
    args = parser.parse_args(argv)

    if not _ID_PATTERN.fullmatch(args.character_id):
        parser.error("character_id must match [a-zA-Z0-9_-]+")

    character_id = args.character_id
    assets_root = _ROOT / "assets" / "characters" / character_id
    data_root = _ROOT / "data" / "characters" / character_id
    if assets_root.exists() or data_root.exists():
        parser.error(f"character already exists: {character_id}")

    images_og = assets_root / "images" / "og"
    images_bg = assets_root / "images" / "bg"
    motions_og = assets_root / "motions" / "og"
    images_og.mkdir(parents=True)
    images_bg.mkdir()
    motions_og.mkdir(parents=True)
    data_root.mkdir(parents=True)

    asset_root = f"assets/characters/{character_id}"
    stamp = _now()
    manifest = {
        "id": character_id,
        "name": args.display_name,
        "created_at": stamp,
        "updated_at": stamp,
        "source_image": f"{asset_root}/images/og/{character_id}.png",
        "source_dir": f"{asset_root}/images/og",
        "motions_dir": f"{asset_root}/motions/og",
        "motions": {"idle": f"{asset_root}/motions/og/idle.webm"},
        "idle_pool": [{"motion": "idle", "weight": 1}],
        "active_variant": "og",
        "selected_generations": {},
        "background_image": f"{asset_root}/images/bg/og.png",
        "background_mode": "follow",
        "voice_id_env_key": "",
        "layout": {},
    }
    personal = {
        "schema_version": 2,
        "display_name": args.display_name,
        "persona": None,
        "skill_refs": [],
        "local_skill_refs": [],
        "skill_overrides": {},
    }
    (assets_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data_root / "personal.json").write_text(json.dumps(personal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {asset_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
