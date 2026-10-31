"""Drawing: layout at every size, rows, previews, the Commands tab and empty states."""

import time
import unittest

from herdscope import app, inventory, render, theme
from herdscope.terminal import Canvas, Style
from herdscope.view import COMMANDS, handle
from tests.support import PREVIEW, Rows, catalog, snapshot


class RenderTests(unittest.TestCase):
    def test_spinner_and_status_icons(self):
        working = inventory.entries(snapshot())[1]
        self.assertNotEqual(render.status_icon(working, 0)[0], render.status_icon(working, 0.1)[0])
        self.assertEqual(render.status_icon(working, 0, symbols=True), ("◐", "yellow"))
        self.assertEqual(render.status_icon(inventory.entries(snapshot())[0], 0), ("●", "red"))

    def draw(self, height, width, entries=None, read=lambda entry, lines: PREVIEW, colors=None, wall=None, **state):
        rows = Rows(entries or inventory.entries(snapshot()))
        view, results = rows.view(**state)
        palette = render.Palette(theme.load({}, colors))
        frame = Canvas(height, width)
        with app.Previewer(Style(), read) as previewer:
            for _ in range(20):
                render.draw(frame, palette, view, rows.entries, view.results(1, rows.entries), 0, previewer, wall=wall)
                if not view.preview or previewer.get(view.selected):
                    break
                time.sleep(0.02)
            render.draw(frame, palette, view, rows.entries, view.results(1, rows.entries), 0, previewer, wall=wall)
        return frame, view

    def test_only_spinners_on_screen_keep_the_frames_coming(self):
        _, view = self.draw(24, 140)
        self.assertTrue(view.hits["animated"], "a working agent is listed")
        _, view = self.draw(24, 140, scope=2)
        self.assertFalse(view.hits["animated"], "only terminals are listed")

    def test_layout_keeps_selection_visible_and_fits_every_size(self):
        base = inventory.entries(snapshot())
        entries = [
            {**base[i % 5], "id": str(i), "key": str(i), "title": f"Task {i} 中文", "order": [i]} for i in range(40)
        ]
        for height, width in ((44, 178), (40, 120), (34, 90), (18, 70), (12, 45), (5, 20)):
            frame, view = self.draw(height, width, entries, selected="39", index=39)
            self.assertTrue(all(len(line) == width for line in frame.cells))
            if height > 6:
                self.assertIn("Task 39", frame.text(), (height, width))

    def test_preview_shows_live_pane_output_beside_results(self):
        frame, _ = self.draw(30, 150)
        text = frame.text()
        self.assertIn("Preview line one", text)
        self.assertIn("Awaiting approval", text)
        self.assertIn("opencode  ·  Project Alpha:1", text)
        self.assertRegex(text, r"Awaiting approval +opencode +Project Alpha:1 +blocked")
        self.assertIn("│", text, "without two tones a line separates list and preview")
        hidden, _ = self.draw(30, 150, preview=False)
        self.assertNotIn("Preview line one", hidden.text())

    def test_preview_failure_is_shown_not_raised(self):
        def fail(entry, lines):
            raise RuntimeError("pane went away")

        frame, _ = self.draw(30, 150, read=fail)
        self.assertIn("Preview unavailable: pane went away", frame.text())

    def test_offline_machine_and_close_confirmation(self):
        entries = inventory.entries(snapshot()) + [inventory.error_entry("Build box", "ssh: connection refused", "bb")]
        frame, view = self.draw(30, 150, entries, selected=entries[3]["id"], confirm=entries[3]["id"])
        text = frame.text()
        self.assertIn("Build box  ssh: connection refused", text)
        self.assertIn("Close terminal in Project Alpha:dev?", text)

    def test_mouse_selects_scopes_and_double_click_accepts(self):
        frame, view = self.draw(30, 150)
        row = next(y for y, index in view.hits["rows"].items() if index == 2)
        self.assertIsNone(handle(view, ("mouse", (0, 10, row, True)), 1.0))
        self.assertEqual(view.index, 2)
        self.assertEqual(handle(view, ("mouse", (0, 10, row, True)), 1.2), "accept")
        start, _, _ = view.hits["tabs"][2]
        handle(view, ("mouse", (0, start + 1, view.hits["tabs_y"], True)), 2.0)
        self.assertEqual(view.scope, 2)

    def test_typing_never_moves_tabs_or_columns(self):
        def positions(query):
            frame, _ = self.draw(30, 150, query=query)
            lines = frame.text().split("\n")
            tabs = next(line for line in lines if "Terminals" in line and "All" in line)
            row = next(line for line in lines if "› nvim  ~/projects" in line)
            return tabs.index("Agents"), tabs.index("Terminals"), row.index("Project Alpha:dev")

        self.assertEqual(positions(""), positions("nvim"))

    def test_status_column_shows_how_long_each_status_has_held(self):
        entries = inventory.entries(snapshot())
        entries[0]["since"] = 1000
        frame, _ = self.draw(30, 150, entries, wall=1000 + 125)
        self.assertRegex(frame.text(), r"Awaiting approval .* blocked +2m")
        self.assertRegex(frame.text(), r"Writing implementation .* working +│", "no time until a change is seen")

    def test_terminal_colors_lift_the_preview_and_selection(self):
        frame, view = self.draw(30, 150, colors=("#b3b3b3", "#000000"))
        self.assertNotIn("│", frame.text(), "two tones need no line")
        row = next(y for y, index in view.hits["rows"].items() if index == view.index)
        self.assertEqual(frame.cells[row][5][1].bg, "#0d0d0d", "the selected row takes the preview's tone")
        self.assertIsNone(frame.cells[row + 1][5][1].bg, "other rows sit on the terminal's background")
        self.assertEqual(frame.cells[10][120][1].bg, "#0d0d0d")

    def test_commands_tab_draws_targets_shortcuts_and_effects(self):
        keys = {"prefix": "ctrl+s", "close_tab": "prefix+&"}
        entries = inventory.entries(snapshot()) + list(catalog(keys=keys).values())
        frame, view = self.draw(30, 150, entries, scope=COMMANDS, query="close tab", cursor=9)
        text = frame.text()
        self.assertIn("> close tab", text)
        self.assertRegex(text, r"× Close tab +tab +Project Alpha:dev +⌃S &")
        self.assertIn("Closes tab dev in Project Alpha and everything running in it:", text)
        self.assertIn("› nvim  ~/projects/alpha", text, "the preview lists what will stop")
        frame, view = self.draw(30, 150, entries, scope=COMMANDS)
        self.assertIn("> run a command", frame.text())

    def test_a_long_old_name_never_hides_what_you_type(self):
        entries = inventory.entries(snapshot()) + list(catalog().values())
        for entry in entries:
            if entry["kind"] == "command" and entry.get("was"):
                entry["was"] = entry["ask"] = "a-very-long-workspace-name-indeed"
        for width in (120, 40):
            view = Rows(entries).view(scope=COMMANDS)[0]
            view.results(1, Rows(entries).entries)
            view.ask(view.current().entry)
            view.edit("ctrl-u")
            view.edit(None, "typed")
            palette = render.Palette(theme.load({}))
            frame = Canvas(24, width)
            with app.Previewer(Style(), lambda entry, lines: "") as previewer:
                render.draw(
                    frame, palette, view, Rows(entries).entries, view.results(1, Rows(entries).entries), 0, previewer
                )
            self.assertIn("typed", frame.text(), width)

    def test_empty_tabs_and_searches_say_so(self):
        terminals = [e for e in inventory.entries(snapshot()) if e["kind"] == "terminal"]
        frame, _ = self.draw(30, 150, terminals, scope=1)
        self.assertIn("No agents running", frame.text())
        frame, _ = self.draw(30, 150, query="zzz")
        self.assertIn("No matches for “zzz”", frame.text())

    def test_only_other_machines_are_named(self):
        remote = inventory.entries(snapshot(), {"id": "gpu", "label": "gpu-box"})[:1]
        frame, _ = self.draw(30, 160, inventory.entries(snapshot()) + remote)
        text = frame.text()
        self.assertNotIn("Local", text, "this machine's panes need no label")
        self.assertRegex(text, r"Awaiting approval +gpu-box +opencode", "a remote pane names its machine")
        frame, _ = self.draw(30, 160, remote)
        self.assertIn("opencode  ·  gpu-box  ·  Project Alpha:1", frame.text(), "and so does its preview")

    def test_footer_keeps_the_most_needed_keys_and_answers(self):
        frame, _ = self.draw(24, 40, preview=False)
        footer = frame.text().split("\n")[-1]
        self.assertIn("↵ go", footer)
        self.assertIn("esc quit", footer, "dropped last, though listed last")
        self.assertNotIn("scroll", footer, "dropped first")
        entries = inventory.entries(snapshot())
        frame, _ = self.draw(24, 60, entries, preview=False, selected=entries[0]["id"], confirm=entries[0]["id"])
        footer = frame.text().split("\n")[-1]
        self.assertIn("y confirm   esc cancel", footer, "the answers always show")
        self.assertIn("Close opencode in Project", footer)

    def test_a_search_that_matches_another_tab_says_which(self):
        frame, _ = self.draw(30, 150, scope=1, query="nvim")
        self.assertIn("Found in Terminals (1) · ⇥ to switch", frame.text())

    def test_folders_keep_their_end_when_cut(self):
        entries = inventory.entries(snapshot())
        entries[3]["cwd"] = "/srv/a/very/long/path/to/the/project/alpha"
        frame, _ = self.draw(30, 80, entries, preview=False)
        self.assertRegex(frame.text(), r"› nvim  …[a-z/]*/project/alpha ")
