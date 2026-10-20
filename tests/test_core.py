from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from offwork_pet.core import Config, ReminderTracker, bubble_visible, countdown, load_config, save_config, validate


class CountdownTests(unittest.TestCase):
    def test_workday_and_boundary(self):
        config = Config()
        self.assertEqual(countdown(datetime(2026, 10, 20, 17, 59, 59), config).display, "00:00:01")
        self.assertEqual(countdown(datetime(2026, 10, 20, 18), config).state, "done")
        self.assertEqual(countdown(datetime(2026, 10, 21, 0), config).seconds, 18 * 3600)
        self.assertEqual(countdown(datetime(2026, 10, 20, 17, 59, 59, 900000), config).seconds, 1)

    def test_weekend_override_and_expiry(self):
        config = Config(override_date="2026-10-24", override_time="17:00")
        self.assertEqual(countdown(datetime(2026, 10, 24, 16), config).seconds, 3600)
        self.assertEqual(countdown(datetime(2026, 10, 25, 16), config).state, "rest")

    def test_clock_jump_and_duplicate_reminders(self):
        tracker = ReminderTracker()
        config = Config()
        def update(hour, minute):
            return tracker.update(countdown(datetime(2026, 10, 20, hour, minute), config))
        self.assertEqual(update(17, 0), [])
        self.assertEqual(update(17, 31), [1800])
        self.assertEqual(update(17, 32), [])
        self.assertEqual(update(18, 2), [0])
        self.assertEqual(update(17, 0), [])
        self.assertEqual(update(18, 0), [])

    def test_restart_after_work_is_quiet(self):
        self.assertEqual(ReminderTracker().update(countdown(datetime(2026, 10, 20, 19), Config())), [])

    def test_config_roundtrip_and_corrupt_file(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = Config(off_time="17:30", color="薄荷绿", x=-200)
            save_config(config, path)
            self.assertEqual(load_config(path), (config, ""))
            path.write_text('{"weekdays": null}', encoding="utf-8")
            loaded, warning = load_config(path)
            self.assertTrue(warning)
            self.assertEqual(loaded, Config())

    def test_old_bubble_config_migration(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for old, expected in (("true", "always"), ("false", "hidden")):
                path.write_text('{"bubble": ' + old + ', "off_time": "17:30"}', encoding="utf-8")
                config, warning = load_config(path)
                self.assertEqual(warning, "")
                self.assertEqual(config.bubble_mode, expected)
                self.assertEqual(config.off_time, "17:30")

    def test_bubble_modes_and_click_expiry(self):
        for mode in ("always", "hover", "click", "hover_click", "hidden"):
            with self.subTest(mode=mode):
                self.assertEqual(bubble_visible(mode, False, 0, 10), mode == "always")
                self.assertEqual(bubble_visible(mode, True, 0, 10), mode in ("always", "hover", "hover_click"))
                self.assertEqual(bubble_visible(mode, False, 12, 10), mode in ("always", "click", "hover_click"))
                self.assertEqual(bubble_visible(mode, False, 12, 12), mode == "always")

    def test_invalid_appearance_config(self):
        for config in (Config(opacity=0), Config(scale=201), Config(bubble_mode="other"), Config(bubble_seconds=0)):
            with self.assertRaises(ValueError):
                validate(config)


if __name__ == "__main__":
    unittest.main()
