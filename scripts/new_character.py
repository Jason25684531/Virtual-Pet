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


def _check(character_id: str) -> int:
    assets_root = _ROOT / "assets" / "characters" / character_id
    manifest_path = assets_root / "manifest.json"
    errors: list[str] = []
    if not _ID_PATTERN.fullmatch(character_id):
        errors.append("character_id must match [a-zA-Z0-9_-]+")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"manifest.json is unreadable: {exc}")
        manifest = {}
    for field in ("id", "name", "motions_dir", "motions"):
        if not manifest.get(field):
            errors.append(f"manifest.{field} is required")
    if manifest.get("id") != character_id:
        errors.append(f"manifest.id must equal {character_id}")
    if manifest.get("id") and not _ID_PATTERN.fullmatch(str(manifest["id"])):
        errors.append("manifest.id must match [a-zA-Z0-9_-]+")
    motions = manifest.get("motions")
    if not isinstance(motions, dict) or not motions.get("idle"):
        errors.append("manifest.motions.idle is required")
    elif not (_ROOT / motions["idle"]).is_file():
        errors.append(f"idle motion is missing: {motions['idle']}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {manifest_path}")
    print(f"OK: idle motion: {motions['idle']}")
    return 0


def _set_preset(character_id: str, enabled: bool) -> int:
    manifest_path = _ROOT / "assets" / "characters" / character_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: manifest.json is unreadable: {exc}")
        return 1
    if manifest.get("id") != character_id:
        print(f"ERROR: manifest.id must equal {character_id}")
        return 1
    manifest["is_preset"] = enabled
    manifest["updated_at"] = _now()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {'marked' if enabled else 'removed'} preset: {character_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a manual character scaffold")
    parser.add_argument("--check", metavar="CHARACTER_ID", help="validate an existing character without writing files")
    parser.add_argument("--make-preset", metavar="CHARACTER_ID", help="show an existing character in the preset list")
    parser.add_argument("--remove-preset", metavar="CHARACTER_ID", help="remove an existing character from the preset list")
    parser.add_argument("character_id", nargs="?")
    parser.add_argument("display_name", nargs="?")
    args = parser.parse_args(argv)

    if args.check:
        if args.make_preset or args.remove_preset or args.character_id or args.display_name:
            parser.error("--check cannot be combined with other arguments")
        return _check(args.check)
    if args.make_preset:
        if args.character_id or args.display_name:
            parser.error("--make-preset cannot be combined with character_id or display_name")
        return _set_preset(args.make_preset, True)
    if args.remove_preset:
        if args.character_id or args.display_name:
            parser.error("--remove-preset cannot be combined with character_id or display_name")
        return _set_preset(args.remove_preset, False)
    if not args.character_id or not args.display_name:
        parser.error("character_id and display_name are required unless using --check")

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
