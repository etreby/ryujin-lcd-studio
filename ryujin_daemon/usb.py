"""USB HID transport for the ASUS Ryujin III Extreme LCD (0b05:1bcb).

Wraps PyUSB. Device-absent and disconnect conditions surface as
``DeviceUnavailable`` so the daemon loop can reconnect without crashing.
"""

from __future__ import annotations

from .daemon import DeviceUnavailable

VENDOR_ID = 0x0B05
PRODUCT_ID = 0x1BCB


class RyujinUsbBackend:
    def __init__(self, *, vendor_id=VENDOR_ID, product_id=PRODUCT_ID,
                 chunk_size=64, endpoint_out=None, timeout_ms=5000):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.chunk_size = chunk_size
        self.endpoint_out = endpoint_out
        self.timeout_ms = timeout_ms
        self._device = None
        self._endpoint = None

    def _find(self):
        import usb.core
        device = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)
        if device is None:
            raise DeviceUnavailable("Ryujin HID device not found")
        return device

    def _configure(self, device):
        try:
            if device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        try:
            device.set_configuration()
        except usb.core.USBError as error:
            raise DeviceUnavailable(f"set configuration failed: {error}")
        if self.endpoint_out is not None:
            self._endpoint = self.endpoint_out
            return
        cfg = device.get_active_configuration()
        for interface in cfg:
            for ep in interface:
                attrs = usb.util.endpoint_direction(ep.bEndpointAddress)
                import usb.util
                if attrs == usb.util.ENDPOINT_OUT:
                    self._endpoint = ep.bEndpointAddress
                    return
        raise DeviceUnavailable("no bulk out endpoint found")

    def open(self, frame=None):
        if self._device is not None:
            return
        try:
            self._device = self._find()
            self._configure(self._device)
        except DeviceUnavailable:
            self._device = None
            raise
        except Exception as error:  # noqa: BLE001
            self._device = None
            raise DeviceUnavailable(str(error))

    def close(self):
        if self._device is not None:
            try:
                import usb.util
                usb.util.dispose_resources(self._device)
            except Exception:  # noqa: BLE001
                pass
        self._device = None
        self._endpoint = None

    def send_frame(self, frame):
        if self._device is None:
            self.open()
        try:
            data = bytes(frame)
            total = len(data)
            offset = 0
            while offset < total:
                chunk = data[offset:offset + self.chunk_size]
                written = self._device.write(self._endpoint, chunk, timeout=self.timeout_ms)
                if written is None or written <= 0:
                    raise DeviceUnavailable("short write on Ryujin HID endpoint")
                offset += written
            return True
        except DeviceUnavailable:
            self.close()
            raise
        except Exception as error:  # noqa: BLE001
            self.close()
            raise DeviceUnavailable(str(error))
