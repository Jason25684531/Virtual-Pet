import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ui.lively_wallpaper import prepare_command


class LivelyWallpaperTest(unittest.TestCase):
    def test_only_active_echoes_receives_valid_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "Lively Wallpaper"
            folder = library / "Library" / "wallpapers" / "echoes"
            folder.mkdir(parents=True)
            executable = root / "Lively.exe"
            executable.touch()
            source = root / "角色 idle.webm"
            source.write_bytes(b"webm-test")
            info = folder / "LivelyInfo.json"
            info.write_text(json.dumps({"Title": "Other wallpaper"}))
            (folder / "LivelyProperties.json").write_text('{"echoesSource": {}}')
            (library / "WallpaperLayout.json").write_text(json.dumps([
                {"LivelyInfoPath": str(folder), "LivelyScreen": {"Index": 1}}
            ]))
            with patch.dict(os.environ, {"LOCALAPPDATA": directory, "LIVELY_EXE": str(executable)}):
                self.assertIsNone(prepare_command(str(source)))
                info.write_text(json.dumps({"Title": "ECHOES Character Host PoC"}))
                command = prepare_command(str(source))
                self.assertEqual(command[:5], [str(executable), "setprop", "--monitor", "1", "--property"])
                target = folder / command[-1].split("=", 1)[1]
                self.assertEqual(target.read_bytes(), b"webm-test")
                with patch("ui.lively_wallpaper.shutil.copy2") as copy:
                    self.assertEqual(prepare_command(str(source)), command)
                    copy.assert_not_called()
                with self.assertRaises(ValueError):
                    prepare_command(str(executable))
                (library / "WallpaperLayout.json").write_text("[]")
                self.assertIsNone(prepare_command(str(source)))


if __name__ == "__main__":
    unittest.main()
