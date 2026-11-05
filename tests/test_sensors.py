import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sensors


class SensorTests(unittest.TestCase):
    def roots(self, base):
        sys = base / "sys"; proc = base / "proc"
        (sys / "class/hwmon").mkdir(parents=True); proc.mkdir()
        (proc / "meminfo").write_text("MemTotal: 1000000 kB\nMemFree: 200000 kB\nMemAvailable: 400000 kB\nSwapTotal: 1000 kB\nSwapFree: 500 kB\n")
        (proc / "stat").write_text("cpu 10 0 10 80 0 0 0 0\n")
        return sys, proc

    def test_hwmon_id_does_not_depend_on_hwmon_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys, proc = self.roots(Path(tmp))
            device = sys / "devices/platform/coretemp.0/hwmon/hwmon9"
            device.mkdir(parents=True); (device / "name").write_text("coretemp")
            (device / "temp1_input").write_text("42000"); (device / "temp1_label").write_text("Package id 0")
            link = sys / "class/hwmon/hwmon1"; link.symlink_to(device)
            first = [x["id"] for x in sensors.discover(sys, proc, include_gpus=False) if x["kind"] == "hwmon"]
            link.unlink(); link = sys / "class/hwmon/hwmon5"; link.symlink_to(device)
            second = [x["id"] for x in sensors.discover(sys, proc, include_gpus=False) if x["kind"] == "hwmon"]
            self.assertEqual(first[0], second[0])

    def test_gpu_ids_use_uuid_and_skip_unavailable_fields(self):
        rows = [dict(uuid="GPU-aaa", name="RTX", temperature="40", utilization="N/A", memory_total="24576", memory_used="100", memory_free="N/A", clock_graphics="1500", clock_memory="9000", power="N/A", fan="30"), dict(uuid="GPU-bbb", name="RTX", temperature="42", utilization="20", memory_total="24576", memory_used="200", memory_free="24000", clock_graphics="1600", clock_memory="9000", power="200", fan="35")]
        with patch.object(sensors, "_nvidia_rows", return_value=rows):
            found = sensors.discover(include_gpus=True)
        ids = [x["id"] for x in found]
        self.assertIn("gpu.GPU-aaa.temperature", ids); self.assertIn("gpu.GPU-bbb.temperature", ids)
        self.assertNotIn("gpu.GPU-aaa.utilization", ids); self.assertNotIn("gpu.GPU-aaa.memory_free", ids)

    def test_unknown_disappeared_and_gpu_timeout_read_as_unavailable(self):
        self.assertIsNone(sensors.read_sensor("gone.sensor"))
        with patch.object(sensors, "_nvidia_rows", side_effect=__import__("subprocess").TimeoutExpired("nvidia-smi", 0.8)):
            self.assertIsNone(sensors.read_sensor("gpu.GPU-aaa.temperature"))

    def test_memory_values_and_missing_hwmon_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys, proc = self.roots(Path(tmp))
            self.assertGreater(sensors.read_sensor("memory.available", sys_root=sys, proc_root=proc), 0)
            self.assertIsNone(sensors.read_sensor("hwmon.missing" , sys_root=sys, proc_root=proc))

    def test_power_hwmon_uses_microwatt_scale_and_disappearing_label_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            sys, proc = self.roots(Path(tmp))
            device = sys / "devices/platform/psu/hwmon/hwmon2"
            device.mkdir(parents=True); (device / "name").write_text("testpsu")
            (device / "power1_input").write_text("125000000")
            (device / "power1_label").write_text("total")
            (sys / "class/hwmon/hwmon0").symlink_to(device)
            item = next(x for x in sensors.discover(sys, proc, include_gpus=False) if x["kind"] == "hwmon")
            self.assertEqual(item["unit"], "W")
            self.assertEqual(sensors.read_sensor(item["id"], sys_root=sys, proc_root=proc), 125)
            (device / "power1_label").unlink()
            self.assertTrue(any(x["id"] == item["id"] for x in sensors.discover(sys, proc, include_gpus=False)))

    def test_batch_gpu_sampling_uses_one_nvidia_query(self):
        rows=[dict(uuid="GPU-aaa",name="RTX",temperature="42",utilization="20",memory_total="24000",memory_used="400",memory_free="23600",clock_graphics="1500",clock_memory="9000",power="200",fan="30")]
        with patch.object(sensors,"_nvidia_rows",return_value=rows) as runner:
            values=sensors.read_sensors(["gpu.GPU-aaa.temperature","gpu.GPU-aaa.utilization"])
        self.assertEqual(values,{"gpu.GPU-aaa.temperature":42.0,"gpu.GPU-aaa.utilization":20.0})
        runner.assert_called_once()


if __name__ == "__main__": unittest.main()
