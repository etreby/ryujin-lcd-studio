import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ryujin_daemon import (
    DeviceUnavailable,
    FramePusherDaemon,
    MockRyujinBackend,
    load_layout,
    load_schema,
)
from ryujin_daemon import cli


class DaemonTests(unittest.TestCase):
    def test_mock_backend_receives_deterministic_frame(self):
        backend = MockRyujinBackend()
        daemon = FramePusherDaemon(backend, width=4, height=2)
        daemon.push_test_frame()
        self.assertEqual(len(backend.frames), 1)
        self.assertEqual(backend.frames[0], bytes(range(8)))

    def test_missing_device_does_not_crash(self):
        backend = MockRyujinBackend(available=False)
        daemon = FramePusherDaemon(backend, width=2, height=2)
        self.assertFalse(daemon.push_test_frame())
        self.assertEqual(backend.frames, [])

    def test_layout_schema_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"
            path.write_text(json.dumps({"width": 640, "height": 480, "format": "gray8"}))
            self.assertEqual(load_layout(path)["width"], 640)

    def test_invalid_layout_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layout.json"
            path.write_text(json.dumps({"width": 640, "height": 480, "format": "rgb"}))
            with self.assertRaises(ValueError):
                load_layout(path)

    def test_schema_export(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "layout.schema.json"
            cli.main(["--export-schema", str(out)])
            schema = json.loads(out.read_text())
            self.assertEqual(schema["properties"]["format"]["enum"], ["gray8"])

    def test_run_reconnects_after_disconnect(self):
        backend = MockRyujinBackend(available=False)
        daemon = FramePusherDaemon(backend, width=2, height=2, fps=100.0)
        sleeps = []

        def sleep(t):
            sleeps.append(t)
            # device becomes available after first retry
            backend.available = True

        pushed = daemon.run(total_frames=1, retry_delay=0.0, sleep=sleep)
        self.assertEqual(pushed, 1)
        self.assertEqual(len(backend.frames), 1)

    def test_run_once_returns_zero_on_absent(self):
        backend = MockRyujinBackend(available=False)
        daemon = FramePusherDaemon(backend, width=2, height=2)
        self.assertEqual(daemon.run(once=True), 0)

    def test_cli_once_mock(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--backend", "mock", "--once", "--width", "2", "--height", "2"])
        self.assertEqual(code, 0)
        self.assertIn("ok", buf.getvalue())

    def test_cli_once_mock_unavailable(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--backend", "mock", "--unavailable", "--once", "--width", "2", "--height", "2"])
        self.assertEqual(code, 1)
        self.assertIn("device unavailable", buf.getvalue())

    def test_schema_is_valid_jsonschema(self):
        schema = load_schema()
        self.assertIn("properties", schema)


if __name__ == "__main__":
    unittest.main()
