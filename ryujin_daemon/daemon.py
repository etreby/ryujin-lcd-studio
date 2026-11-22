"""Frame transport loop with a deterministic smoke-test frame.

The loop pushes the deterministic frame at the configured frame rate and
recovers from device-absent, disconnect, and reconnect conditions without
crashing. Backends implement ``send_frame(frame)`` and raise
``DeviceUnavailable`` when the LCD is absent; ``open()``/``close()`` are
optional hooks used by hardware backends.
"""

import time


class DeviceUnavailable(RuntimeError):
    """Raised by a backend when the USB device is absent."""


class FramePusherDaemon:
    def __init__(self, backend, *, width=640, height=480, fps=1.0):
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.backend = backend
        self.width = width
        self.height = height
        self.fps = float(fps)
        self.interval = 1.0 / self.fps
        self._frame = self._deterministic_frame()

    def _deterministic_frame(self):
        size = self.width * self.height
        frame = bytes(range(256)) * ((size + 255) // 256)
        return frame[:size]

    def push_test_frame(self):
        """Push one deterministic frame. Returns True when accepted."""
        try:
            self.backend.send_frame(self._frame)
        except DeviceUnavailable:
            return False
        return True

    def _call(self, name):
        hook = getattr(self.backend, name, None)
        return hook if callable(hook) else (lambda *a: None)

    def run(self, *, once=False, total_frames=None, retry_delay=2.0, sleep=time.sleep, log=None):
        """Push frames until done; reconnect on DeviceUnavailable.

        ``once`` pushes a single frame (one attempt). ``total_frames`` pushes
        exactly that many accepted frames, reconnecting as needed. Without
        either, the loop runs until interrupted. Returns the accepted frame
        count.
        """
        pushed = 0
        while True:
            try:
                self._call("open")()
                self.backend.send_frame(self._frame)
                pushed += 1
                if log:
                    log(f"frame {pushed} pushed ({self.width}x{self.height} @ {self.fps:g} fps)")
                if once or (total_frames is not None and pushed >= total_frames):
                    return pushed
                sleep(self.interval)
            except DeviceUnavailable as error:
                if log:
                    log(f"device unavailable: {error}; retrying in {retry_delay:g}s")
                self._call("close")()
                if once:
                    return pushed
                sleep(retry_delay)
            except KeyboardInterrupt:
                self._call("close")()
                return pushed
