from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from character_library import CharacterLibrary


class _LayoutLibrary(CharacterLibrary):
    def __init__(self, manifest):
        self._manifest = manifest

    def get_character(self, _character_id):
        return self._manifest


class CharacterLayoutTests(unittest.TestCase):
    def test_missing_layout_returns_empty_config(self):
        library = _LayoutLibrary({"id": "demo"})
        self.assertEqual(library.get_layout_config("demo"), {})

    def test_manifest_layout_returns_copy(self):
        layout = {
            "character_x_offset": 12,
            "character_y_offset": -20,
            "character_scale": 1.15,
            "object_position": "center bottom",
        }
        library = _LayoutLibrary({"id": "demo", "layout": layout})
        config = library.get_layout_config("demo")

        self.assertEqual(config, layout)
        self.assertIsNot(config, layout)

    def test_idle_motion_candidates_include_base_and_specialty_entries(self):
        with tempfile.TemporaryDirectory(prefix="echoes-idle-pool-") as temp_dir:
            motions_dir = Path(temp_dir)
            idle_path = motions_dir / "Idle.webm"
            guitar_path = motions_dir / "Idle_Guitar.webm"
            dj_path = motions_dir / "Idle_DJ.webm"
            idle_path.write_bytes(b"idle")
            guitar_path.write_bytes(b"guitar")
            dj_path.write_bytes(b"dj")

            library = _LayoutLibrary(
                {
                    "id": "miku",
                    "motions_dir": str(motions_dir),
                    "motions": {"idle": str(idle_path)},
                    "idle_pool": [
                        {"motion": "idle", "weight": 5},
                        {"filename": "Idle_Guitar.webm", "weight": 2},
                        {"filename": "Idle_DJ.webm", "weight": 1},
                    ],
                }
            )

            candidates = library.get_idle_motion_candidates("miku")

            self.assertEqual(
                candidates,
                [
                    {"path": str(idle_path), "weight": 5},
                    {"path": str(guitar_path), "weight": 2},
                    {"path": str(dj_path), "weight": 1},
                ],
            )

    def test_idle_motion_candidates_filter_missing_specialty_files(self):
        with tempfile.TemporaryDirectory(prefix="echoes-idle-pool-missing-") as temp_dir:
            motions_dir = Path(temp_dir)
            idle_path = motions_dir / "Idle.webm"
            idle_path.write_bytes(b"idle")

            library = _LayoutLibrary(
                {
                    "id": "Choppr",
                    "motions_dir": str(motions_dir),
                    "motions": {"idle": str(idle_path)},
                    "idle_pool": [
                        {"motion": "idle", "weight": 4},
                        {"filename": "Idle_reading.webm", "weight": 2},
                    ],
                }
            )

            candidates = library.get_idle_motion_candidates("Choppr")

            self.assertEqual(candidates, [{"path": str(idle_path), "weight": 4}])


if __name__ == "__main__":
    unittest.main()
