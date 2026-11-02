"""The development tools in tools/, kept working as the code they drive changes."""

import shutil
import sys
import unittest

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "tools"))

import benchmark
import screenshot
import social_preview


class ToolTests(unittest.TestCase):
    def test_benchmark_measures_every_kind_of_work(self):
        results = benchmark.measure(60)
        self.assertEqual(
            set(results), {"first frame", "keystroke", "keystroke, worst", "backspace", "refresh", "frame"}
        )
        self.assertTrue(all(value > 0 for value in results.values()))

    def test_screenshot_draws_the_demo_session(self):
        tokens = screenshot.theme.load({"theme": {"name": "catppuccin"}})
        html = screenshot.page(screenshot.frame(tokens), tokens)
        for text in ("Migrate router to typed routes", "gpu-box", "Allow Codex to edit", "Herdscope"):
            self.assertIn(text, html)

    def test_social_preview_shows_every_agent_and_no_preview(self):
        tokens = social_preview.theme.load({"theme": {"name": "catppuccin"}})
        html = social_preview.page(social_preview.frame(tokens), tokens)
        for _, _, _, _, task, *_ in (*screenshot.AGENTS, *screenshot.REMOTE):
            self.assertIn(task, html)
        self.assertNotIn("Allow Codex to edit", html)

    @unittest.skipUnless(shutil.which("fzf"), "fzf is not installed")
    def test_benchmark_ranks_panes_exactly_as_fzf_does(self):
        self.assertTrue(benchmark.against_fzf(300)["same ranking"])
