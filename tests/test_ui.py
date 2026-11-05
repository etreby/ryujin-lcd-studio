import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PyQt5 import QtWidgets
import app as studio


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self, config_dir):
        win = studio.Window(config_dir=config_dir, dry_run=True)
        import time
        deadline = time.monotonic()+2
        while win.sensor_worker and win.sensor_worker.isRunning() and time.monotonic()<deadline:
            self.qt.processEvents(); time.sleep(.01)
        self.qt.processEvents()
        return win

    def test_malformed_profile_errors_without_startup_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            dashboard = folder / "dashboard.json"
            dashboard.write_text("{broken", encoding="utf-8")
            before = dashboard.read_bytes()
            with patch.object(studio.subprocess, "run", side_effect=AssertionError("must not call systemd")):
                with self.assertRaisesRegex(ValueError, "Cannot read dashboard.json"):
                    self.window(folder)
            self.assertEqual(dashboard.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["dashboard.json"])

    def test_preview_custom_stat_never_executes_its_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            win = self.window(Path(tmp))
            win.config["layout"] = {
                "custom": {"stats": [{"stat": "custom", "indicator": "GPU", "command": ["touch", "/tmp/must-not-exist"], "unit": "°C"}]}
            }
            with patch.object(studio.subprocess, "run", side_effect=AssertionError("preview executed a command")):
                win.render()
            self.assertFalse(win.pixmap.isNull())
            win.dirty = False
            win.close()

    def test_select_host_sensor_persists_driver_command_and_refresh_keeps_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            win = self.window(Path(tmp))
            win.sensor_items = [{"id":"memory.available","label":"Memory available","unit":"GB"}]
            win.config["layout"] = {"custom":{"stats":[{"stat":"liquid_temperature","indicator":"RAM"}]}}
            win.populate_cards()
            combo = win.stat_rows.itemAt(0).widget().findChild(QtWidgets.QComboBox)
            original = __import__("copy").deepcopy(win.config["layout"]["custom"]["stats"][0])
            win.change_stat(win.config["layout"]["custom"]["stats"][0],None)
            self.assertEqual(win.config["layout"]["custom"]["stats"][0],original)
            index = combo.findData("sensor:memory.available")
            self.assertGreaterEqual(index, 0)
            combo.setCurrentIndex(index)
            stat = win.config["layout"]["custom"]["stats"][0]
            self.assertEqual(stat["stat"], "custom")
            self.assertEqual(stat["unit"], "GB")
            self.assertEqual(stat["command"], [str(studio.host_sensors.PYTHON), str(studio.host_sensors.APP_DIR/"sensors.py"), "--read", "memory.available"])
            self.assertTrue(win.dirty)
            before = __import__("copy").deepcopy(win.config)
            with patch.object(studio.host_sensors, "discover", return_value=[{"id":"memory.available","label":"Memory available","unit":"GB"}]):
                win.refresh_sensors()
                deadline = __import__("time").time()+2
                while win.sensor_worker.isRunning() and __import__("time").time()<deadline:
                    self.qt.processEvents(); __import__("time").sleep(.01)
                self.qt.processEvents()
            self.assertEqual(win.config, before)
            self.assertTrue(win.dirty)
            win.dirty=False; win.close()

    def test_recognized_sensor_restores_selection_and_disappearance_stays_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            win = self.window(Path(tmp))
            command=[str(studio.host_sensors.PYTHON),str(studio.host_sensors.APP_DIR/"sensors.py"),"--read","memory.available"]
            stat={"stat":"custom","indicator":"RAM","command":command,"unit":"GB"}
            win.sensor_items=[{"id":"memory.available","label":"Memory available","unit":"GB"}]
            win.config["layout"]={"one":{"stats":[stat]}}
            win.populate_cards()
            combo=win.stat_rows.itemAt(0).widget().findChild(QtWidgets.QComboBox)
            self.assertEqual(combo.currentData(),"sensor:memory.available")
            win.sensor_items=[]; win.load_card()
            combo=win.stat_rows.itemAt(0).widget().findChild(QtWidgets.QComboBox)
            self.assertEqual(combo.currentData(),"sensor:memory.available")
            self.assertIn("Unavailable",combo.currentText())
            win.dirty=False;win.close()

    def test_static_and_animated_media_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            still = folder / "still.png"
            Image.new("RGB", (32, 24), "red").save(still)
            gif = folder / "animated.gif"
            Image.new("RGB", (32, 24), "red").save(
                gif, save_all=True, append_images=[Image.new("RGB", (32, 24), "blue")],
                duration=80, loop=0,
            )
            win = self.window(folder)
            win.options.update(mode="static", path=str(still))
            win.mode.setCurrentIndex(win.mode.findData("static"))
            self.assertFalse(win.pixmap.isNull())
            win.options.update(mode="gif", path=str(gif))
            win.mode.setCurrentIndex(win.mode.findData("gif"))
            self.assertFalse(win.pixmap.isNull())
            self.assertIsNotNone(win.movie)
            win.movie.stop()
            win.dirty = False
            win.close()

    def test_grouped_sensor_choices_show_live_value_without_dirtying_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            win=self.window(Path(tmp))
            win.sensor_items=[
                {"id":"memory.free","label":"Memory free","unit":"GB"},
                {"id":"memory.available","label":"Memory available","unit":"GB"},
                {"id":"gpu.GPU-aaa.temperature","uuid":"GPU-aaa","label":"GPU 1 · RTX temperature","unit":"°C"},
            ]
            win.config["layout"]={"one":{"stats":[{"stat":"liquid_temperature","indicator":"COOLANT"}]}}
            win.populate_cards()
            combo=win.stat_rows.itemAt(0).widget().findChild(QtWidgets.QComboBox)
            self.assertGreaterEqual(combo.findText("— Memory & swap —"),0)
            self.assertGreaterEqual(combo.findText("— GPU 1 —"),0)
            win.live_enabled=True
            win.config["layout"]["one"]["stats"][0].update(stat="custom",unit="°C",command=[str(studio.host_sensors.PYTHON),str(studio.host_sensors.APP_DIR/"sensors.py"),"--read","gpu.GPU-aaa.temperature"])
            win.sensor_items=[win.sensor_items[-1]]
            win.populate_cards()
            with patch.object(studio,"_render_lcd_stats",wraps=studio._render_lcd_stats) as renderer:
                win.sensor_sampled({"gpu.GPU-aaa.temperature":57.0})
                custom_values=renderer.call_args.kwargs["custom_values"]
            self.assertEqual(custom_values[0],("57","°C"))
            self.assertTrue(any("57 °C" in label.text() for label,_ in win.stat_preview_labels))
            self.assertFalse(win.dirty)
            win.dirty=False;win.close()

    def test_selection_change_marks_dirty_and_apply_button_tracks_dirty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            win=self.window(Path(tmp))
            self.assertFalse(win.apply_btn.isEnabled())
            win.general_changed()
            self.assertTrue(win.apply_btn.isEnabled())
            self.assertTrue(win.revert_btn.isEnabled())
            win.dirty=False;win.close()

    def test_revert_discards_unsaved_edits_and_keeps_disk_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); win=self.window(folder)
            original=win.title_edit.text()
            win.title_edit.setText('Unsaved title')
            self.assertTrue(win.dirty)
            with patch.object(QtWidgets.QMessageBox,'question',return_value=QtWidgets.QMessageBox.Discard):
                win.revert()
            self.assertEqual(win.title_edit.text(),original)
            self.assertFalse(win.dirty); self.assertFalse(win.apply_btn.isEnabled())
            self.assertEqual(list(folder.iterdir()),[])
            win.close()

    def test_live_preview_marks_stale_and_unavailable_values_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            win=self.window(Path(tmp)); win.live_enabled=True
            sensor_id='memory.available'
            stat={'stat':'custom','indicator':'RAM','unit':'GB','command':[str(studio.host_sensors.PYTHON),str(studio.host_sensors.APP_DIR/'sensors.py'),'--read',sensor_id]}
            win.sensor_items=[{'id':sensor_id,'label':'Memory available','unit':'GB'}]
            win.sensor_values[sensor_id]=3.2; win.sensor_timestamps[sensor_id]=__import__('time').monotonic()-10
            self.assertIn('stale',win.sensor_preview_text(stat))
            win.sensor_values[sensor_id]=None; win.sensor_timestamps[sensor_id]=__import__('time').monotonic()
            self.assertIn('N/A',win.sensor_preview_text(stat))
            win.dirty=False;win.close()

    def test_arbitrary_custom_command_never_enters_live_sampling(self):
        with tempfile.TemporaryDirectory() as tmp:
            win=self.window(Path(tmp))
            win.live_enabled=True
            win.config["layout"]={"one":{"stats":[{"stat":"custom","indicator":"Mystery","unit":"","command":["sh","-c","touch /tmp/should-not-run"]}]}}
            with patch.object(studio.host_sensors,"read_sensors",side_effect=AssertionError("no recognized sensor IDs")):
                win.sample_selected()
            win.render()
            self.assertEqual(win.selected_sensor_ids(),[])
            win.dirty=False;win.close()


if __name__ == "__main__":
    unittest.main()
