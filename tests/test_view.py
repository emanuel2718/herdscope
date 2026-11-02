"""The view model: results, selection, line editing and key handling."""

import unittest

from herdscope import commands, fuzzy, inventory
from herdscope.view import COMMANDS, Item, View, handle
from tests.support import Rows, catalog, snapshot


class ViewTests(unittest.TestCase):
    def test_rows_show_tasks_commands_and_real_directories(self):
        items = [Item(e) for e in inventory.entries(snapshot())]
        self.assertEqual(items[0].fields["label"], "Awaiting approval")
        self.assertEqual(items[0].fields["place"], "Project Alpha:1")
        self.assertEqual((items[3].fields["label"], items[3].fields["detail"]), ("nvim", "~/projects/alpha"))
        self.assertEqual((items[4].fields["label"], items[4].fields["detail"]), ("~/projects/alpha", ""))
        opencode = Item({**inventory.entries(snapshot())[0], "title": "OC | Fix the bug"})
        self.assertEqual(opencode.fields["label"], "Fix the bug")

    def test_match_positions_map_back_to_fields(self):
        item = Item(inventory.entries(snapshot())[3])
        _, positions = fuzzy.Query("nvim dev").match(item.text)
        self.assertEqual(item.highlights(positions), {"label": {0, 1, 2, 3}, "place": {14, 15, 16}})

    def test_query_resets_to_best_match_but_refresh_keeps_selection(self):
        rows = Rows(inventory.entries(snapshot()))
        view, results = rows.view()
        view.move(2)
        selected = view.selected
        reordered = list(reversed(rows.entries))
        view.results(2, reordered)
        self.assertEqual(view.selected, selected)
        view.edit(None, "nvim")
        view.results(2, reordered)
        self.assertEqual(view.current().fields["label"], "nvim")
        view.scope = 1
        self.assertTrue(all(i.kind == "agent" for i in view.results(2, reordered)))

    def test_typing_on_and_refreshing_rank_like_starting_over(self):
        entries = inventory.entries(snapshot()) + commands.catalog(snapshot(), {})
        view = View()
        for version, query in enumerate(["o", "op", "ope", "ope ", "ope a", "ope aw", "ope a", "!nvim", "a | p"]):
            entries[0] = {**entries[0], "terminal_title_stripped": f"Awaiting approval {version}"}
            view.query = query
            typed = [(item.id, view.highlights(item)) for item in view.results(version // 3, entries)]
            fresh = View()
            fresh.query = query
            self.assertEqual(typed, [(item.id, fresh.highlights(item)) for item in fresh.results(0, entries)], query)

    def test_close_targets_the_confirmed_pane_even_if_selection_moves(self):
        rows = Rows(inventory.entries(snapshot()))
        view, _ = rows.view()
        view.move(3)
        target = view.selected
        handle(view, ("key", "ctrl-x"), 0)
        view.results(2, [e for e in rows.entries if e["id"] != target])
        self.assertNotEqual(view.selected, target)
        self.assertEqual(handle(view, ("text", "y"), 0), "close")
        self.assertEqual(view.closing, target)
        handle(view, ("key", "ctrl-x"), 0)
        self.assertIsNone(handle(view, ("text", "n"), 0), "any other key cancels")
        self.assertIsNone(view.confirm)

    def test_commands_mode_keys(self):
        entries = sorted(inventory.entries(snapshot()), key=lambda e: inventory.rank(e, {})) + list(catalog().values())
        view = View()
        view.scope = 1
        view.results(1, entries)
        handle(view, ("text", ">ren"), 0)
        self.assertEqual((view.scope, view.query), (COMMANDS, "ren"), "> is the prompt, not the query")
        view.results(1, entries)
        self.assertEqual(view.current().entry["title"], "Rename workspace…")
        self.assertIsNone(handle(view, ("key", "enter"), 0))
        self.assertEqual((view.query, view.cursor), ("Project Alpha", 13), "the input starts from the old name")
        handle(view, ("key", "ctrl-u"), 0)
        handle(view, ("text", "Beta"), 0)
        self.assertEqual(view.current().entry["title"], "Rename workspace…", "typing a name doesn't re-rank")
        handle(view, ("key", "esc"), 0)
        self.assertEqual((view.prompt, view.query), (None, "ren"), "Esc goes back to the search")
        handle(view, ("key", "enter"), 0)
        handle(view, ("text", "x"), 0)
        self.assertEqual(handle(view, ("key", "enter"), 0), "run")
        self.assertEqual(view.running, view.prompt)
        view.unask()
        view.edit("ctrl-u")
        view.results(1, entries)
        view.edit(None, "close tab")
        view.results(1, entries)
        self.assertIsNone(handle(view, ("key", "enter"), 0), "destructive commands ask first")
        self.assertEqual(handle(view, ("text", "y"), 0), "run")
        view.edit("ctrl-u")
        handle(view, ("key", "backspace"), 0)
        self.assertEqual(view.scope, 1, "Backspace on an empty command search goes back")
        view.results(1, entries)
        self.assertTrue(all(i.kind == "agent" for i in view.rows))
        view.scope = 0
        view.results(1, entries)
        self.assertNotIn("command", {i.kind for i in view.rows}, "All lists panes only")

    def test_only_y_confirms(self):
        rows = Rows(inventory.entries(snapshot()))
        view, _ = rows.view()
        for key in (("key", "enter"), ("key", "ctrl-x"), ("text", "n")):
            handle(view, ("key", "ctrl-x"), 0)
            self.assertIsNone(handle(view, key, 0), key)
            self.assertIsNone(view.confirm)
        handle(view, ("key", "ctrl-x"), 0)
        self.assertEqual(handle(view, ("text", "Y"), 0), "close")

    def test_double_click_does_what_enter_does(self):
        entries = Rows(inventory.entries(snapshot()) + list(catalog().values())).entries
        view = View()
        view.scope = COMMANDS
        view.results(1, entries)
        view.hits = {"rows": {6: 1}, "tabs": [], "tabs_y": 3, "preview": None}
        handle(view, ("mouse", (0, 5, 6, True)), 1.0)
        self.assertIsNone(handle(view, ("mouse", (0, 5, 6, True)), 1.2))
        self.assertEqual(view.prompt, view.selected, "a command asks for its name, never tries to focus")

    def test_clicks_in_the_preview_leave_the_selection_alone(self):
        rows = Rows(inventory.entries(snapshot()))
        view, _ = rows.view()
        selected = view.selected
        view.hits = {"rows": {6: 2}, "tabs": [], "tabs_y": 3, "preview": (40, 0, 40, 20)}
        for at in (1.0, 1.1):  # A double-click, as when selecting a word.
            self.assertIsNone(handle(view, ("mouse", (0, 50, 6, True)), at))
        self.assertEqual(view.selected, selected)
        handle(view, ("mouse", (0, 5, 6, True)), 2.0)
        self.assertNotEqual(view.selected, selected, "the same line in the list still picks its row")

    def test_a_paste_loses_its_escapes_and_control_characters(self):
        rows = Rows(inventory.entries(snapshot()))
        view, _ = rows.view()
        handle(view, ("paste", "ab\x1b[2Jc\x08d\x00e\n f"), 0)
        self.assertEqual(view.query, "abcde f")

    def test_double_clicking_a_command_that_confirms_waits_for_the_answer(self):
        entries = Rows(inventory.entries(snapshot()) + list(catalog().values())).entries
        view = View()
        view.scope = COMMANDS
        view.query = "close tab"
        rows = view.results(1, entries)
        line = next(i for i, item in enumerate(rows) if item.entry["title"] == "Close tab")
        view.hits = {"rows": {6: line}, "tabs": [], "tabs_y": 3, "preview": None}
        for pressed, at in ((True, 1.0), (False, 1.05), (True, 1.2), (False, 1.25)):
            handle(view, ("mouse", (0, 5, 6, pressed)), at)
        self.assertEqual(view.confirm, "command:close_tab", "the second click's release doesn't answer")
        self.assertEqual(handle(view, ("text", "y"), 2), "run")

    def test_selection_change_resets_the_preview_scroll(self):
        rows = Rows(inventory.entries(snapshot()))
        view, _ = rows.view()
        view.preview_scroll = 9
        view.edit(None, "nvim")
        view.results(1, rows.entries)
        self.assertEqual(view.preview_scroll, 0)

    def test_line_editing_follows_readline(self):
        view = View()
        view.edit(None, "alpha beta gamma")
        view.edit("alt-b")
        self.assertEqual(view.cursor, 11)
        view.edit("ctrl-w")
        self.assertEqual((view.query, view.cursor), ("alpha gamma", 6))
        view.edit("alt-d")
        self.assertEqual(view.query, "alpha ")
        view.edit("ctrl-a")
        view.edit("ctrl-f")
        view.edit("ctrl-u")
        self.assertEqual((view.query, view.cursor), ("lpha ", 0))
