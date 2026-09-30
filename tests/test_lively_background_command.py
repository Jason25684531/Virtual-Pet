import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ui.lively_wallpaper import prepare_background_command


class LivelyBackgroundCommandTest(unittest.TestCase):
    def test_background_command_targets_only_background_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "Lively Wallpaper" / "Library" / "wallpapers" / "background"
            folder.mkdir(parents=True)
            executable = root / "Lively.exe"
            executable.touch()
            source = root / "room.jpg"
            source.write_bytes(b"background")
            (folder / "LivelyInfo.json").write_text(json.dumps({"Title": "ECHOES Background Host"}))
            (folder / "LivelyProperties.json").write_text(json.dumps({"echoesBackground": {}}))
            (root / "Lively Wallpaper" / "WallpaperLayout.json").write_text(json.dumps([
                {"LivelyInfoPath": str(folder), "LivelyScreen": {"Index": 1}}
            ]))
            with patch.dict(os.environ, {"LOCALAPPDATA": directory, "LIVELY_EXE": str(executable)}):
                command = prepare_background_command(str(source))
            self.assertEqual(command[:5], [str(executable), "setprop", "--monitor", "1", "--property"])
            target = folder / command[-1].split("=", 1)[1]
            self.assertEqual(target.read_bytes(), b"background")


if __name__ == "__main__":
    unittest.main()
