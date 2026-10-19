"""Russian preferences use the maintained Ukrainian translation everywhere."""

import json
from pathlib import Path
import unittest
from component_loader import load_component_module

mod = load_component_module("localization")
events = load_component_module("schedule_events")


class LocalizationTests(unittest.TestCase):
    def test_language_alias_and_backend_calendar_titles(self):
        for language in ("uk", "uk-UA", "ru", "ru-RU", "RU", "ru_UA"):
            with self.subTest(language=language):
                self.assertTrue(mod.is_ukrainian(language))
                for kind in ("outage", "possible"):
                    self.assertEqual(events.event_summary(kind, language), events.event_summary(kind, "uk"))
        for language in (None, "", "en", "en-GB", "de"):
            self.assertFalse(mod.is_ukrainian(language))

    def test_native_ha_alias_matches_complete_ukrainian_file(self):
        root = Path(__file__).resolve().parents[1] / "custom_components/dtek_monitor/translations"
        self.assertEqual(json.loads((root / "ru.json").read_text()), json.loads((root / "uk.json").read_text()),
                         "Run python scripts/sync_translations.py after updating Ukrainian translations")
