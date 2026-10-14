import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ui.lively_wallpaper import LivelyWallpaper, lively_command, prepare_command


class LivelyWallpaperTest(unittest.TestCase):
    def test_installer_command_runs_directly(self):
        self.assertEqual(lively_command("C:/Lively/Lively.exe", "setwp", "--file", "room image.png"),
                         ["C:/Lively/Lively.exe", "setwp", "--file", "room image.png"])

    def test_store_command_uses_package_context_and_quotes_arguments(self):
        executable = "C:/Program Files/WindowsApps/LivelyWallpaper_1.0_x64__publisher/Build/Lively.exe"
        command = lively_command(executable, "setwp", "--file", "C:/User's room/image.png")
        self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertIn("Invoke-CommandInDesktopPackage -PackageFamilyName 'LivelyWallpaper_publisher'", command[-1])
        self.assertIn("-Args 'setwp --file \"C:/User''s room/image.png\"'", command[-1])
        self.assertIn("-PreventBreakaway -ErrorAction Stop", command[-1])

    def test_store_commands_reuse_one_powershell(self):
        wallpaper = LivelyWallpaper.__new__(LivelyWallpaper)
        wallpaper._shell = None
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Invoke-X"]
        with patch("ui.lively_wallpaper.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            wallpaper._send("a.png", lambda _: command)
            wallpaper._send("b.png", lambda _: command)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], [*command[:-1], "-"])
        self.assertEqual(popen.return_value.stdin.write.call_count, 2)
        popen.return_value.stdin.write.assert_called_with("Invoke-X\n")

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
