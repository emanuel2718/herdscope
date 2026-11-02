"""Colors: Herdr's themes, the terminal's own palette, and overrides."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdscope import commands, render, theme
from herdscope.terminal import Style
from tests.support import snapshot


class ThemeTests(unittest.TestCase):
    def test_dim_is_the_terminal_text_dimmed(self):
        palette = render.Palette(theme.load({}))
        self.assertEqual(palette("overlay1", "sidebar_bg"), Style(None, None, False, True, False))
        self.assertEqual(palette("text", "surface0"), Style(None, None, False, False, False), "never a dim background")

    def test_theme_follows_herdr_tokens_and_overrides(self):
        tokens = theme.load(
            {
                "theme": {"name": "tokyo-night", "custom": {"red": "#123", "panel_bg": "reset"}},
                "ui": {"accent": "magenta", "status_indicators": "symbols"},
            }
        )
        self.assertEqual(tokens["red"], "#112233")
        self.assertIsNone(tokens["panel_bg"])
        self.assertEqual(tokens["accent"], 5)
        self.assertTrue(tokens["symbols"])
        self.assertEqual(theme.load({"theme": {"name": "nope"}})["text"], theme.THEMES["catppuccin"]["text"])
        self.assertEqual(theme.color("rgb(17, 24, 39)", None), "#111827")

    def test_without_a_herdr_theme_colors_come_from_the_terminal(self):
        tokens = theme.load({}, terminal=("#b3b3b3", "#000000"))
        self.assertIsNone(tokens["text"])
        self.assertIsNone(tokens["sidebar_bg"], "the list sits on the terminal's own background")
        self.assertEqual(tokens["panel_bg"], "#0d0d0d")
        self.assertEqual((tokens["red"], tokens["green"], tokens["yellow"]), (1, 2, 3))
        self.assertEqual(tokens["accent"], theme.THEMES["catppuccin"]["accent"], "Herdr's accent, as on its border")
        self.assertTrue(tokens["lifted"])
        light = theme.load({}, terminal=("#2a2c33", "#f9f9f9"))
        self.assertLess(int(light["panel_bg"][1:3], 16), 0xF9, "light terminals lift toward the text color")
        passive = theme.load({})
        self.assertIsNone(passive["panel_bg"])
        self.assertFalse(passive["lifted"])
        self.assertEqual(theme.load({"ui": {"accent": "cyan"}}, ("#ffffff", "#000000"))["accent"], 6)
        self.assertEqual(theme.load({"theme": {"name": "nord"}}, ("#ffffff", "#000000"))["panel_bg"], "#2e3440")
        self.assertEqual(theme.load({"theme": {"name": "terminal"}}, ("#ffffff", "#000000"))["panel_bg"], "#121212")

    def test_settings_of_the_wrong_type_count_as_unset(self):
        default = theme.load({})
        for settings in (
            {"theme": "dracula"},
            {"theme": {"name": ["dracula"]}},
            {"theme": {"custom": "x"}},
            {"theme": {"auto_switch": True, "dark_name": 5, "light_name": None}},
            {"ui": "x"},
            {"keys": "x"},
            {"keys": {"new_tab": 5, "prefix": ["ctrl+b"], "split_right": [3]}},
        ):
            tokens = theme.load(settings, ("#cdd6f4", "#1e1e2e"))
            self.assertEqual(tokens["accent"], default["accent"], settings)
            self.assertTrue(commands.catalog(snapshot(), settings), settings)

    def test_light_themes_and_auto_switch_follow_the_terminal(self):
        for name in ("one-light", "solarized-light", "kanagawa-lotus", "rose-pine-dawn"):
            self.assertIn(name, theme.THEMES)
        settings = {"theme": {"auto_switch": True, "dark_name": "nord", "light_name": "rose-pine-dawn"}}
        self.assertEqual(theme.load(settings, ("#575279", "#faf4ed"))["panel_bg"], "#faf4ed", "a light terminal")
        self.assertEqual(theme.load(settings, ("#e0e0e0", "#101010"))["panel_bg"], "#2e3440", "a dark one")
        config = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, config)
        (config / "herdr").mkdir()
        (config / "herdr/config.toml").write_text('[ui]\naccent = "red"\n')
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config), "HERDR_CONFIG_PATH": ""}):
            self.assertEqual(theme.config(), {"ui": {"accent": "red"}})
