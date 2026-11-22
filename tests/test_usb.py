"""Chunked frame transmission tests for the Ryujin USB backend."""

import unittest
from unittest.mock import MagicMock, patch

from ryujin_daemon import DeviceUnavailable, FramePusherDaemon, MockRyujinBackend, RyujinUsbBackend


class FakeEndpoint:
    def __init__(self, address=0x01):
        self.bEndpointAddress = address


class UsbBackendTests(unittest.TestCase):
    def _backend_with_device(self, chunk_size=16):
        backend = RyujinUsbBackend(chunk_size=chunk_size)
        device = MagicMock()
        device.write = MagicMock(side_effect=lambda ep, data, timeout: len(data))
        backend._device = device
        backend._endpoint = 0x01
        return backend, device

    def test_frame_is_chunked(self):
        backend, device = self._backend_with_device(chunk_size=16)
        frame = bytes(40)
        backend.send_frame(frame)
        writes = device.write.call_args_list
        self.assertEqual(len(writes), 3)  # 16 + 16 + 8
        self.assertEqual([len(c.args[1]) for c in writes], [16, 16, 8])

    def test_short_write_raises_unavailable(self):
        backend, device = self._backend_with_device()
        device.write.side_effect = None
        device.write.return_value = 0
        with self.assertRaises(DeviceUnavailable):
            backend.send_frame(bytes(10))

    def test_transport_error_maps_to_unavailable(self):
        backend, device = self._backend_with_device()
        device.write.side_effect = OSError("no device")
        with self.assertRaises(DeviceUnavailable):
            backend.send_frame(bytes(10))
        self.assertIsNone(backend._device)

    def test_zero_chunk_size_rejected(self):
        with self.assertRaises(Exception):
            backend = RyujinUsbBackend(chunk_size=0)
            backend.send_frame(bytes(10))

    def test_fallback_to_mock_when_hardware_absent(self):
        backend = MockRyujinBackend()
        daemon = FramePusherDaemon(backend, width=8, height=8, fps=60.0)
        self.assertTrue(daemon.push_test_frame())


if __name__ == "__main__":
    unittest.main()
