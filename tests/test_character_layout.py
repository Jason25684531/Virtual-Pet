from __future__ import annotations

import sys
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
