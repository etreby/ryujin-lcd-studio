import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backend


CONFIG = {
    "title": "KEEP THIS",
    "font_color": "B300FF",
    "background_color": "100713",
    "interval": 5,
    "layout": {
        "custom": {
            "stats": [{"stat": "custom", "indicator": "GPU", "command": ["nvidia-smi", "--query", "temperature.gpu"], "unit": "°C"}],
            "offset_x": 0,
            "offset_y": 0,
            "background_opacity": 0.5,
        }
    },
}


class FakeRunner:
    def __init__(self, fail_start=False):
        self.active = True
        self.commands = []
        self.fail_start = fail_start

    def run(self, args, **kwargs):
        args = list(args)
        self.commands.append(args)
        if args[2:4] == ["is-active", "--quiet"]:
            return subprocess.CompletedProcess(args, 0 if self.active else 3, "", "")
        if args[2:3] == ["stop"]:
            self.active = False
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[2:3] == ["start"]:
            self.active = not self.fail_start
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[2:3] == ["show"]:
            state = "ActiveState=active\nNRestarts=0\n" if self.active else "ActiveState=inactive\nNRestarts=1\n"
            return subprocess.CompletedProcess(args, 0, state, "")
        return subprocess.CompletedProcess(args, 0, "", "")


class BackendTests(unittest.TestCase):
    def manager(self, root, runner=None, dry_run=False):
        return backend.ApplyManager(
            root / "profile", dry_run=dry_run, runner=runner,
            service_path=root / "systemd" / "ryujin-lcd.service",
            cli=Path("/home/test/.local/bin/ryujin-lcd"), validator=lambda config: None,
        )

    def test_dry_run_validates_without_creating_anything_or_calling_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = FakeRunner()
            manager = self.manager(root, runner, dry_run=True)
            self.assertEqual(manager.apply(CONFIG, {"mode": "stats"}), root / "profile")
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(runner.commands, [])

    def test_apply_backs_up_existing_files_and_writes_user_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "profile"
            profile.mkdir()
            (profile / "dashboard.json").write_text('{"old":true}')
            runner = FakeRunner()
            backup = self.manager(root, runner).apply(CONFIG, {"mode": "stats"})
            self.assertEqual(json.loads((profile / "dashboard.json").read_text()), CONFIG)
            self.assertTrue((backup / "dashboard.json").is_file())
            self.assertTrue((profile / "display.json").is_file())
            unit = (root / "systemd/ryujin-lcd.service").read_text()
            self.assertIn("Type=simple", unit)
            self.assertIn("RemainAfterExit=yes", unit)
            self.assertIn("KillSignal=SIGINT", unit)
            self.assertIn("NRestarts", runner.commands[-1])
            self.assertTrue(runner.active)

    def test_partial_file_write_failure_restores_every_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "profile"
            profile.mkdir()
            service = root / "systemd/ryujin-lcd.service"
            service.parent.mkdir()
            files = {
                profile / "dashboard.json": b'{"before":"dashboard"}',
                profile / "display.json": b'{"before":"display"}',
                service: b"prior service unit\n",
            }
            for path, data in files.items():
                path.write_bytes(data)
            original_atomic = backend.atomic_json

            def fail_after_write(path, value):
                original_atomic(path, value)
                if path.name == "display.json":
                    raise OSError("simulated disk fault after replace")

            with patch.object(backend, "atomic_json", side_effect=fail_after_write):
                with self.assertRaises(backend.ApplyError):
                    self.manager(root, FakeRunner()).apply(CONFIG, {"mode": "stats"})
            for path, data in files.items():
                self.assertEqual(path.read_bytes(), data)

    def test_service_start_failure_restores_existing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "profile"
            profile.mkdir()
            old = b'{"title":"old"}'
            (profile / "dashboard.json").write_bytes(old)
            with self.assertRaises(backend.ApplyError):
                self.manager(root, FakeRunner(fail_start=True)).apply(CONFIG, {"mode": "stats"})
            self.assertEqual((profile / "dashboard.json").read_bytes(), old)

    def test_bad_json_is_reported_without_repair_or_startup_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboard.json"
            invalid = b'{not json'
            path.write_bytes(invalid)
            with self.assertRaisesRegex(ValueError, "Cannot read dashboard.json"):
                backend.read_json(path)
            self.assertEqual(path.read_bytes(), invalid)
            self.assertFalse((path.parent / backend.SERVICE_NAME).exists())

    def test_service_unit_quotes_paths_with_spaces(self):
        unit = backend.service_unit(Path("/tmp/test venv/python"), Path("/tmp/app.py"),
                                    Path("/tmp/profile with space"), Path("/tmp/ryujin-lcd"))
        self.assertIn('"/tmp/test venv/python"', unit)
        self.assertIn('"/tmp/profile with space"', unit)

    def test_driver_schema_accepts_string_colors_and_rejects_invalid_fields(self):
        backend.validate_stats_config(CONFIG)
        invalid = dict(CONFIG)
        invalid["mystery_setting"] = True
        with self.assertRaisesRegex(ValueError, "unknown stats configuration key"):
            backend.validate_stats_config(invalid)

    def test_gif_mode_requires_animation_and_accepts_animated_gif(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            still = root / "still.png"
            Image.new("RGB", (8, 8), "red").save(still)
            with self.assertRaisesRegex(ValueError, "animated GIF"):
                backend.validate_display({"mode": "gif", "path": str(still)})
            animated = root / "animated.gif"
            Image.new("RGB", (8, 8), "red").save(
                animated, save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")],
                duration=100, loop=0,
            )
            self.assertEqual(backend.validate_display({"mode": "gif", "path": str(animated)}),
                             ["gif", str(animated.resolve())])


if __name__ == "__main__":
    unittest.main()
