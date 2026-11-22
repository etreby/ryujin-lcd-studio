"""In-memory HID backend used when hardware is unavailable."""

from .daemon import DeviceUnavailable


class MockRyujinBackend:
    def __init__(self, *, available=True):
        self.available = available
        self.frames = []

    def send_frame(self, frame):
        if not self.available:
            raise DeviceUnavailable("Ryujin HID device is unavailable")
        self.frames.append(bytes(frame))
