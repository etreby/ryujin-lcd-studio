"""Isolated configuration and user-service operations for Ryujin LCD Studio."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable, Sequence


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = Path.home() / ".config/ryujin-lcd"
SERVICE_NAME = "ryujin-lcd.service"


class CommandRunner:
    """Small injectable wrapper around subprocess.run."""

    def run(self, args: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(list(args), **kwargs)


def read_json(path: Path, *, default=None):
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"Configuration file not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def validate_display(options: dict) -> list[str]:
    mode = options.get("mode", "stats")
    if mode not in {"stats", "static", "gif"}:
        raise ValueError("Choose Live dashboard, Static picture, or Animated GIF.")
    if mode == "stats":
        return ["stats"]
    path = options.get("path", "")
    if not path or not Path(path).is_file():
        raise ValueError("Choose an existing picture or GIF first.")
    from PIL import Image

    try:
        with Image.open(path) as image:
            animated = bool(getattr(image, "is_animated", False))
            image_format = image.format
            image.verify()
            if mode == "gif" and not animated:
                raise ValueError("Choose an animated GIF for GIF mode.")
            if mode == "static" and image_format == "GIF" and animated:
                # Static mode intentionally displays the first frame.
                pass
    except (OSError, ValueError) as exc:
        raise ValueError(f"The selected media cannot be opened: {exc}") from exc
    return [mode, str(Path(path).resolve())]


def validate_stats_config(config: dict) -> None:
    """Validate using the installed experimental driver's parser, without execution."""
    from copy import deepcopy
    from liquidctl.driver.asus_ryujin_lcd import _validate_lcd_stats_config

    # Validate in memory so relative image paths keep their original meaning
    # (the display driver resolves them relative to dashboard.json).
    _validate_lcd_stats_config(deepcopy(config))


def service_unit(python: Path, app: Path, config_dir: Path, cli: Path) -> str:
    # systemd unit arguments use C-style quoting; escape backslashes and quotes.
    def quote(arg: str) -> str:
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'

    exec_start = " ".join(map(quote, (str(python), str(app), "--run-display", "--config-dir", str(config_dir), "--cli", str(cli))))
    return (
        "[Unit]\nDescription=Ryujin III Extreme LCD display\nAfter=graphical-session.target\n\n"
        "[Service]\nType=simple\nRemainAfterExit=yes\nRestart=on-failure\n"
        "RestartSec=5\nKillSignal=SIGINT\nTimeoutStopSec=10\n"
        f"ExecStart={exec_start}\n"
        "\n[Install]\nWantedBy=default.target\n"
    )


class ApplyError(RuntimeError):
    pass


class ApplyManager:
    """Write profile files, then hand display ownership to the user service.

    dry_run mode validates only: no file writes or systemctl calls.
    """

    def __init__(self, config_dir: Path, *, dry_run=False, runner=None,
                 python: Path | None = None, app: Path = APP_DIR / "app.py",
                 cli: Path | None = None, service_path: Path | None = None,
                 validator: Callable = validate_stats_config):
        self.config_dir = Path(config_dir)
        self.dry_run = dry_run
        self.runner = runner or CommandRunner()
        self.python = python or Path(os.sys.executable)
        self.app = Path(app)
        self.cli = cli or Path.home() / ".local/bin/ryujin-lcd"
        self.service_path = service_path or (
            self.config_dir / SERVICE_NAME if dry_run else
            Path.home() / ".config/systemd/user" / SERVICE_NAME
        )
        self.validator = validator

    def _systemctl(self, *args, check=True):
        if self.dry_run:
            return subprocess.CompletedProcess(["systemctl", *args], 0, "", "")
        result = self.runner.run(["systemctl", "--user", *args], capture_output=True,
                                 text=True, timeout=30)
        if check and result.returncode:
            raise ApplyError(result.stderr.strip() or f"systemctl {' '.join(args)} failed")
        return result

    def apply(self, config: dict, options: dict) -> Path:
        self.validator(config)
        validate_display(options)
        if self.dry_run:
            # The UI's --config-dir mode is a pure preview: validation only,
            # with no config, service, or systemd writes.
            return self.config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with (self.config_dir / ".apply.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._apply_live(config, options)

    def _apply_live(self, config: dict, options: dict) -> Path:
        self.service_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard, display = self.config_dir / "dashboard.json", self.config_dir / "display.json"
        targets = (dashboard, display, self.service_path)
        saved = {path: path.read_bytes() if path.exists() else None for path in targets}
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = self.config_dir / "backups" / stamp
        backup.mkdir(parents=True, exist_ok=False)
        for path, content in saved.items():
            if content is not None:
                shutil.copy2(path, backup / path.name)
        was_active = self._systemctl("is-active", "--quiet", SERVICE_NAME, check=False).returncode == 0
        mutated = False
        try:
            if was_active:
                self._systemctl("stop", SERVICE_NAME)
            mutated = True
            atomic_json(dashboard, config)
            atomic_json(display, options)
            unit_tmp = self.service_path.with_name(f".{self.service_path.name}.tmp")
            unit_tmp.write_text(service_unit(self.python, self.app, self.config_dir, self.cli), encoding="utf-8")
            os.replace(unit_tmp, self.service_path)
            self._systemctl("daemon-reload")
            self._systemctl("start", SERVICE_NAME)
            import time
            time.sleep(1)
            status = self.runner.run(
                ["systemctl", "--user", "show", SERVICE_NAME,
                 "-p", "ActiveState", "-p", "NRestarts"],
                capture_output=True, text=True, timeout=10,
            )
            if (status.returncode or "ActiveState=active" not in status.stdout
                    or "NRestarts=0" not in status.stdout):
                raise ApplyError(status.stderr.strip() or "Display service did not become active")
            return backup
        except Exception as exc:
            if mutated:
                self._systemctl("stop", SERVICE_NAME, check=False)
                for path, content in saved.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_name(f".{path.name}.rollback")
                        tmp.write_bytes(content)
                        os.replace(tmp, path)
                self._systemctl("daemon-reload", check=False)
                if was_active:
                    self._systemctl("start", SERVICE_NAME, check=False)
            raise ApplyError(f"Could not apply settings; previous files were restored. {exc}") from exc
