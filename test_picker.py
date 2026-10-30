import copy
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch

import commands
import fuzzy
import picker
import picker_ui
import theme
from terminal_ui import Canvas, Style, Terminal, parse_ansi, parse_input, rgb, sgr

PREVIEW = "\x1b[38;2;137;180;250mPreview\x1b[0m line one\r\n\x1b[1;31mfailure\x1b[0m\r\n"


def snapshot():
    statuses = [("blocked", "Awaiting approval"), ("working", "Writing implementation"), ("idle", "Ready for a task")]
    panes = [{"pane_id": f"w1:p{i}", "terminal_id": f"term-{i}", "workspace_id": "w1", "tab_id": "w1:t1",
              "agent": "opencode", "agent_status": status, "terminal_title_stripped": title}
             for i, (status, title) in enumerate(statuses)]
    panes += [{"pane_id": "w1:p7", "terminal_id": "term-7", "workspace_id": "w1", "tab_id": "w1:t2",
               "terminal_title_stripped": "nvim ~/p/alpha", "cwd": str(Path.home() / "projects/alpha")},
              {"pane_id": "w1:p8", "terminal_id": "term-8", "workspace_id": "w1", "tab_id": "w1:t2",
               "terminal_title_stripped": "~/p/alpha", "cwd": str(Path.home() / "projects/alpha")}]
    return {
        "focused_pane_id": "w1:p8",
        "workspaces": [{"workspace_id": "w1", "label": "Project Alpha", "number": 1}],
        "tabs": [{"tab_id": "w1:t1", "label": "1", "number": 1}, {"tab_id": "w1:t2", "label": "dev", "number": 2}],
        "panes": panes,
    }


class Rows:
    """Inventory stand-in for drawing tests."""

    def __init__(self, entries):
        # Like the picker: ranked panes, then the command catalog in its own order.
        self.entries = sorted((e for e in entries if e["kind"] != "command"), key=lambda e: picker.rank(e, {})) + \
            [e for e in entries if e["kind"] == "command"]

    def view(self, **state):
        view = picker_ui.View()
        results = view.results(1, self.entries)
        for key, value in state.items():
            setattr(view, key, value)
        return view, results


def catalog(actions=(), agents=(), keys=None, workspaces=1):
    """Commands for the snapshot fixture, opened from the ~/projects/alpha shell in tab "dev"."""
    data = snapshot()
    data["workspaces"] += [{"workspace_id": f"w{i}", "label": f"Space {i}", "number": i}
                           for i in range(2, workspaces + 1)]
    with patch.object(commands.shutil, "which", side_effect=lambda name: name in agents):
        return {c["id"].split(":", 1)[1]: c for c in commands.catalog(data, {"keys": keys or {}}, actions)}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("STATE", Path(self.temp.name)), ("SOCKET", f"{self.temp.name}/herdr.sock")):
            patcher = patch.object(picker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_attention_order_then_frecency_and_current_pane_sinks(self):
        items = picker.entries(snapshot())
        frequent = copy.deepcopy(items[1])
        frequent.update(id="another", key="frequent")
        scores = {items[2]["key"]: (10000, 0), frequent["key"]: (10, 0)}
        ordered = sorted([items[4], items[3], items[2], items[1], frequent, items[0]],
                         key=lambda e: picker.rank(e, scores))
        self.assertEqual([e["id"] for e in ordered],
                         [items[0]["id"], "another", items[1]["id"], items[2]["id"], items[3]["id"], items[4]["id"]])
        self.assertTrue(items[4]["current"])

    def test_frecency_persists_decays_and_deduplicates_hook(self):
        picker.record("pane", now=100)
        picker.record("pane", now=101)
        with picker.database() as db:
            self.assertEqual(db.execute("SELECT weight FROM visits").fetchone()[0], 1)
        picker.record("pane", now=100 + picker.HALF_LIFE)
        with picker.database() as db:
            self.assertEqual(db.execute("SELECT weight FROM visits").fetchone()[0], 1.5)
        self.assertEqual(picker.history()["pane"][1], 100 + picker.HALF_LIFE)

    def test_machine_session_identity_and_pane_moves(self):
        data = snapshot()
        a = picker.entries(data, {"id": "machine-a", "label": "Box", "session": "agents"})[0]
        b = picker.entries(data, {"id": "machine-b", "label": "Box", "session": "agents"})[0]
        other = picker.entries(data, {"id": "machine-a", "label": "Box", "session": "other"})[0]
        self.assertEqual(len({a["key"], b["key"], other["key"]}), 3)
        data["panes"][0]["pane_id"] = "w2:p4"
        moved = picker.entries(data, {"id": "machine-a", "label": "Renamed", "session": "agents"})[0]
        self.assertEqual(a["key"], moved["key"])
        self.assertEqual(len(picker.entries(data, {"id": "m", "label": "Box"})), 3, "remote terminals are omitted")

    def test_focus_hook_records_the_visit_and_clears_out_closed_panes(self):
        data, folder = snapshot(), picker.status_dir()
        folder.mkdir(parents=True)
        for name in ("w1:p0", "w9:p1"):  # A live agent's file and a closed one's
            (folder / name).write_text("idle\n")
        for pane_id in ("w1:p0", "w9:p1"):  # The second closed before its hook ran: nothing to record.
            event = {"event": "pane_focused", "data": {"pane_id": pane_id}}
            with patch.dict(os.environ, {"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}), \
                    patch.object(picker, "rpc", return_value={"snapshot": data}):
                picker.record_visit()
        self.assertEqual(list(picker.history()), [picker.entry_key(["socket", picker.SOCKET], data["panes"][0])])
        self.assertEqual(os.listdir(folder), ["w1:p0"])

    def test_status_hook_records_when_each_status_began(self):
        items, path = picker.entries(snapshot()), picker.status_dir() / "w1:p0"
        key = items[0]["key"]

        def hook(status, pane="w1:p0", at=None):
            # Herdr's compact JSON. A title can't pose as a field: its quotes are escaped.
            event = json.dumps({"event": "pane_agent_status_changed", "data": {
                "pane_id": pane, "title": 'say "agent_status":"done"', "agent_status": status}}, separators=(",", ":"))
            subprocess.run(["sh", "status.sh"], check=True, cwd=Path(__file__).parent, env={
                **os.environ, "HERDR_PLUGIN_STATE_DIR": str(picker.STATE), "HERDR_SOCKET_PATH": picker.SOCKET,
                "HERDR_PLUGIN_EVENT_JSON": event})
            if at:
                os.utime(path, (at, at))

        hook("working", at=100)
        hook("working")
        self.assertEqual(picker.statuses(items), {key: ("working", 100)}, "repeats keep the first time")
        hook("blocked", at=200)
        hook("done", pane="../w1:p0")  # Pane IDs name files, so odd ones are ignored.
        hook("bogus")
        self.assertEqual(picker.statuses(items), {key: ("blocked", 200)})
        self.assertEqual(os.listdir(picker.status_dir()), ["w1:p0"])
        self.assertEqual(picker.history(), {}, "a status change is not a visit")
        Path(picker.SOCKET).touch()  # Herdr restarted, so pane IDs may name other panes now.
        self.assertEqual(picker.statuses(items), {})

    def test_time_in_state_comes_from_the_hook_or_what_the_picker_saw(self):
        items = picker.entries(snapshot())
        blocked, working, idle = items[:3]
        recorded = {blocked["key"]: ("blocked", 50), working["key"]: ("idle", 10)}
        seen = {}
        picker.stamp(items, recorded, seen, now=100)
        self.assertEqual([blocked["since"], working["since"], idle["since"]], [50, None, None])
        self.assertNotIn("since", items[3], "terminals have no status")
        idle["status"] = "working"
        picker.stamp(items, recorded, seen, now=130)
        self.assertEqual(idle["since"], 130, "a change seen while open counts from when it was seen")

    def test_open_action_asks_herdr_and_tolerates_a_second_press(self):
        fake = Path(self.temp.name) / "herdr"
        for reply, code, expected in (("{}", 0, 0), ('{"error":{"message":"a popup pane is already open"}}', 1, 0),
                                      ('{"error":{"message":"plugin not found"}}', 1, 1)):
            # Like Herdr's CLI: errors go to stderr.
            fake.write_text(f"#!/bin/sh\necho \"$*\" > {fake}.args\necho '{reply}' >&{2 if code else 1}\nexit {code}\n")
            fake.chmod(0o755)
            result = subprocess.run(["sh", "open.sh"], env={**os.environ, "HERDR_BIN_PATH": str(fake)},
                                    capture_output=True, text=True, cwd=Path(__file__).parent)
            self.assertEqual(result.returncode, expected, reply)
            self.assertEqual(Path(f"{fake}.args").read_text().split()[:3], ["plugin", "pane", "open"])
        self.assertIn("plugin not found", result.stderr)

    def test_cli_errors_show_herdr_s_message(self):
        fake = Path(self.temp.name) / "herdr"
        error = json.dumps({"error": {"code": "machine_unreachable", "message": "box is offline"}})
        fake.write_text(f"#!/bin/sh\necho '{error}' >&2\nexit 1\n")
        fake.chmod(0o755)
        with patch.object(picker, "HERDR", str(fake)):
            with self.assertRaisesRegex(RuntimeError, "^box is offline$"):
                picker.cli(["api", "snapshot"])

    def test_old_history_is_forgotten(self):
        picker.record("old", now=100)
        picker.record("new", now=100 + picker.FORGET + 1)
        self.assertEqual(set(picker.history()), {"new"})

    def test_refresh_survives_unexpected_errors(self):
        replies = iter([{"snapshot": snapshot()}])
        def rpc(method, params=None):
            reply = next(replies, None)
            if reply is None:
                raise TypeError("odd reply")
            return reply
        with patch.object(picker, "rpc", rpc):
            inventory = picker.Inventory()
            inventory.poke()
            worker = threading.Thread(target=inventory.refresh, daemon=True)
            worker.start()
            time.sleep(0.2)
            inventory.stop.set()
            inventory.poked.set()
            worker.join(1)
        self.assertEqual(inventory.sources["local"][0]["kind"], "error", "an error row, not a dead thread")
        self.assertIn("odd reply", inventory.sources["local"][0]["error"])

    def test_many_machines_share_a_few_connections(self):
        machines = [{"id": f"m{i}", "label": f"Box {i}"} for i in range(40)]
        running, peak, lock = 0, 0, threading.Lock()

        def cli(args, stop=None, **kwargs):
            nonlocal running, peak
            if args[0] == "machine":
                return machines
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.01)
            with lock:
                running -= 1
            return {"snapshot": snapshot()}

        with patch.object(picker, "rpc", return_value={"snapshot": snapshot()}), patch.object(picker, "cli", cli):
            with picker.Inventory() as inventory:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and any(
                        e["kind"] == "error" for e in inventory.current()[1] if e["machine_id"]):
                    time.sleep(0.02)
                loaded = {e["machine_id"] for e in inventory.current()[1] if e["kind"] == "agent"}
        self.assertEqual(loaded, {None} | {m["id"] for m in machines})
        self.assertLessEqual(peak, picker.MACHINE_WORKERS)
        self.assertEqual(len(inventory.workers), 2 + picker.MACHINE_WORKERS, "not a thread per machine")

    def test_the_invoking_pane_is_where_the_popup_came_from(self):
        with patch.object(picker, "rpc", return_value={"snapshot": snapshot()}), \
                patch.object(picker, "ORIGIN", "w1:p7"):
            inventory = picker.Inventory()
        self.assertEqual(inventory.snapshot["focused_pane_id"], "w1:p7", "not the other client's focus (w1:p8)")
        self.assertEqual([e["pane_id"] for e in inventory.sources["local"] if e["current"]], ["w1:p7"])

    def test_remote_focus_never_uses_local_ids(self):
        entry = picker.entries(snapshot(), {"id": "remote-id", "label": "Remote", "session": "agents"})[0]
        with patch.object(picker, "cli", side_effect=[{"agent": {"terminal_id": "term-0"}}, {}]) as cli, \
                patch.object(picker, "rpc") as rpc:
            picker.focus(entry)
        rpc.assert_not_called()
        self.assertEqual(cli.call_args_list[0].args[0], ["--machine", "remote-id", "agent", "get", "w1:p0"])
        self.assertEqual(cli.call_args_list[1].args[0], ["--machine", "remote-id", "agent", "focus", "w1:p0"])

    def test_stale_destination_does_not_focus_close_or_record(self):
        entry = picker.entries(snapshot())[0]
        for action in (picker.focus, picker.close):
            with patch.object(picker, "rpc", return_value={"pane": {"terminal_id": "replacement"}}) as rpc:
                with self.assertRaisesRegex(RuntimeError, "replaced"):
                    action(entry)
            self.assertEqual(rpc.call_count, 1)
        self.assertEqual(picker.history(), {})

    def test_close_validates_then_closes_local_panes_only(self):
        entry = picker.entries(snapshot())[3]
        with patch.object(picker, "rpc", side_effect=[{"pane": {"terminal_id": "term-7"}}, {}]) as rpc:
            picker.close(entry)
        self.assertEqual(rpc.call_args_list[1].args, ("pane.close", {"pane_id": "w1:p7"}))
        remote = picker.entries(snapshot(), {"id": "r", "label": "Remote"})[0]
        with self.assertRaisesRegex(RuntimeError, "other machines"):
            picker.close(remote)


class CommandTests(unittest.TestCase):
    def test_shortcuts_read_like_keys(self):
        self.assertEqual(commands.chord("prefix+shift+n", "ctrl+s"), "⌃S ⇧N")
        self.assertEqual(commands.chord(["prefix+m", "prefix+%"], "ctrl+b"), "⌃B m")
        self.assertEqual(commands.chord("prefix+comma", "ctrl+s"), "⌃S ,")
        self.assertEqual(commands.chord("alt+enter"), "⌥↵")
        self.assertEqual(commands.chord(""), "")
        found = catalog(keys={"prefix": "ctrl+s", "rename_tab": "prefix+comma", "new_tab": ["prefix+.", "prefix+c"]})
        self.assertEqual(found["rename_tab"]["binding"], "⌃S ,", "the user's binding")
        self.assertEqual(found["new_tab"]["binding"], "⌃S .", "the first of several")
        self.assertEqual(found["new_workspace"]["binding"], "⌃S ⇧N", "Herdr's default, with the user's prefix")

    def test_commands_target_where_the_popup_was_opened(self):
        found = catalog(actions=[{"plugin_id": "someone.notes", "action_id": "jot", "title": "Jot a note"},
                                 {"plugin_id": picker.PLUGIN, "action_id": "open", "title": "Herdscope"}],
                        agents=("claude",))
        self.assertEqual(found["rename_workspace"]["target"], "Project Alpha")
        self.assertEqual(found["rename_tab"]["target"], "Project Alpha:dev")
        self.assertEqual(found["split_right"]["target"], "~/projects/alpha")
        self.assertEqual(found["close_tab"]["confirm"], "Close tab dev in Project Alpha? Its 2 panes stop.")
        self.assertEqual(len(found["close_tab"]["affects"]), 2)
        self.assertIn("start_claude", found)
        self.assertNotIn("start_codex", found, "only agents that are installed")
        self.assertIn("plugin:someone.notes:jot", found)
        self.assertNotIn(f"plugin:{picker.PLUGIN}:open", found, "never itself")
        self.assertNotIn("move_workspace_up", found, "a single workspace has nowhere to move")
        self.assertNotIn("new_worktree", found, "not a Git checkout")
        self.assertEqual(found["rename_workspace"]["title"], "Rename workspace…", "… marks commands that ask")

    def test_each_command_makes_the_right_call(self):
        found = catalog(agents=("claude",), workspaces=3)
        calls = []
        def rpc(method, params=None):
            calls.append((method, params))
            return {"root_pane": {"pane_id": "w1:p9"}}
        with patch.object(picker, "rpc", rpc), patch.object(commands.subprocess, "Popen") as popen:
            found["rename_workspace"]["run"]("  Renamed  ")
            found["split_right"]["run"](None)
            found["close_tab"]["run"](None)
            found["move_workspace_down"]["run"](None)
            found["rename_pane"]["run"]("")
            found["start_claude"]["run"](None)
            with self.assertRaisesRegex(RuntimeError, "Type a name first"):
                found["rename_tab"]["run"]("   ")
        self.assertEqual(calls[:5], [
            ("workspace.rename", {"workspace_id": "w1", "label": "Renamed"}),
            ("pane.split", {"target_pane_id": "w1:p8", "direction": "right", "focus": True,
                            "cwd": str(Path.home() / "projects/alpha")}),
            ("tab.close", {"tab_id": "w1:t2"}),
            ("workspace.move_block", {"workspace_ids": ["w1"], "before_workspace_id": "w3"}),
            ("pane.rename", {"pane_id": "w1:p8", "label": None})])
        self.assertEqual(calls[5][0], "tab.create")
        self.assertEqual(popen.call_args.args[0][3:], [picker.HERDR, "claude-w1-p9", "claude", "w1:p9"],
                         "a name no other agent has: the new pane's")
        self.assertTrue(popen.call_args.kwargs["start_new_session"], "the agent's start outlives the popup")

    def test_agent_start_retries_then_reports_a_failure(self):
        fake = Path(tempfile.mkdtemp()) / "herdr"
        self.addCleanup(shutil.rmtree, fake.parent)
        fake.write_text(f"#!/bin/sh\necho \"$*\" >> {fake}.calls\ncase $1 in agent) exit 1;; esac\n")
        fake.chmod(0o755)
        subprocess.run(["sh", "-c", commands.START, str(fake), "claude-w1-p9", "claude", "w1:p9"], check=True)
        calls = Path(f"{fake}.calls").read_text().splitlines()
        self.assertEqual(calls[:2], ["agent start claude-w1-p9 --kind claude --pane w1:p9"] * 2)
        self.assertEqual(calls[2], "notification show Herdscope --body Couldn't start claude in the new tab")

    def test_other_plugins_run_after_the_popup_and_wording_fits(self):
        found = catalog(actions=[{"plugin_id": "someone.notes", "action_id": "jot", "title": "Jot"}])
        with patch.object(commands.subprocess, "Popen") as popen:
            found["plugin:someone.notes:jot"]["run"](None)
        self.assertEqual(popen.call_args.args[0][:2], ["sh", "-c"])
        self.assertIn("sleep", popen.call_args.args[0][2], "after the popup closes, so its own popup can open")
        self.assertEqual(popen.call_args.args[0][-4:], ["plugin", "action", "invoke", "someone.notes.jot"])
        self.assertEqual(found["close_workspace"]["confirm"], "Close workspace Project Alpha? Its 5 panes stop.")
        self.assertEqual(found["rename_pane"]["ask"], "~/projects/alpha", "the input starts from the current name")
        self.assertIsNone(found["rename_tab"]["blank"], "a tab needs a name")
        self.assertEqual(found["rename_pane"]["blank"], "its own title")


class FuzzyTests(unittest.TestCase):
    TEXTS = ["Replacing Vue’s TypeScript 6 compatib… rana:4 opencode idle ~/git/work/rana",
             "nvim ~/git/work/rana rana:1", "~/git/ranamono-v2 ranamono-v2:1", "src/components/UserProfile.vue",
             "FooBar baz_qux 123abc", "a/b/c/d.e f-g h:i", "abc abc abcabc",
             "Vue-router setup review rana:3 claude done",
             "Résumé café naïve", "cafe resume naive"]

    @unittest.skipUnless(shutil.which("fzf"), "fzf is not installed")
    def test_ranking_matches_the_fzf_binary(self):
        queries = ["rana vue", "rana", "vue", "nv", "wrk", "'rana", "^nvim", "rana$", "!rana", "rana | foo", "'abc'",
                   "FB", "fb", "Bar", "!^nvim rana", "s/c/U", "123", "done$", "ue", "a", "rv", "gwr", "^~/g",
                   "cafe", "naive", "resume", "Resume", "é", "ï", "!café", "^Rés"]
        for query in queries:
            expected = subprocess.run(["fzf", "--filter", query, "--tiebreak=index"], input="\n".join(self.TEXTS),
                                      text=True, capture_output=True, env={**os.environ, "FZF_DEFAULT_OPTS": ""})
            self.assertEqual([t for t, _ in fuzzy.rank(self.TEXTS, query)], expected.stdout.splitlines(), query)

    def test_highlights_the_best_alignment_not_the_leftmost(self):
        text = self.TEXTS[0]
        score, positions = fuzzy.Query("rana vue").match(text)
        self.assertEqual("".join(text[i] for i in positions), "Vuerana")
        self.assertEqual(positions[:3], [text.index("Vue") + i for i in range(3)])

    def test_case_folding_keeps_one_character_per_position(self):
        for text in ("İstanbul", "ΟΔΟΣ ΣΟΦΙΑ", "Straße", "Ünïcödé"):
            self.assertEqual(len(fuzzy.lower(text)), len(text), text)
        self.assertEqual(fuzzy.Query("stanbul").match("İstanbul")[1], list(range(1, 8)))
        self.assertEqual(fuzzy.normalize("Ünïcödé × Æ"), "Unicode × Æ")

    def test_extended_syntax_and_smart_case(self):
        match = lambda query, text: fuzzy.Query(query).match(text) is not None
        self.assertTrue(match("'work", "~/git/work"))
        self.assertFalse(match("'wrk", "~/git/work"))
        self.assertTrue(match("^nvim", "  nvim x"))
        self.assertFalse(match("^vim", "nvim"))
        self.assertTrue(match("rana$", "work/rana"))
        self.assertFalse(match("!rana", "work/rana"))
        self.assertTrue(match("!rana", "vault"))
        self.assertTrue(match("vault | rana", "work/rana"))
        self.assertTrue(match("'abc'", "x abc y"))
        self.assertFalse(match("'abc'", "abcabc"))
        self.assertFalse(match("Vue", "vue"))
        self.assertTrue(match("vue", "Vue"))
        self.assertTrue(match("a\\ b", "a b"))
        self.assertFalse(match("a\\ b", "ab"))


class DisplayTests(unittest.TestCase):
    def test_rows_show_tasks_commands_and_real_directories(self):
        items = [picker_ui.Item(e) for e in picker.entries(snapshot())]
        self.assertEqual(items[0].fields["label"], "Awaiting approval")
        self.assertEqual(items[0].fields["place"], "Project Alpha:1")
        self.assertEqual((items[3].fields["label"], items[3].fields["detail"]), ("nvim", "~/projects/alpha"))
        self.assertEqual((items[4].fields["label"], items[4].fields["detail"]), ("~/projects/alpha", ""))
        opencode = picker_ui.Item({**picker.entries(snapshot())[0], "title": "OC | Fix the bug"})
        self.assertEqual(opencode.fields["label"], "Fix the bug")

    def test_match_positions_map_back_to_fields(self):
        item = picker_ui.Item(picker.entries(snapshot())[3])
        _, positions = fuzzy.Query("nvim dev").match(item.text)
        self.assertEqual(item.highlights(positions), {"label": {0, 1, 2, 3}, "place": {14, 15, 16}})

    def test_query_resets_to_best_match_but_refresh_keeps_selection(self):
        rows = Rows(picker.entries(snapshot()))
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
        self.assertTrue(all(i.entry["kind"] == "agent" for i, _ in view.results(2, reordered)))

    def test_close_targets_the_confirmed_pane_even_if_selection_moves(self):
        rows = Rows(picker.entries(snapshot()))
        view, _ = rows.view()
        view.move(3)
        target = view.selected
        picker_ui.handle(view, ("key", "ctrl-x"), 0)
        view.results(2, [e for e in rows.entries if e["id"] != target])
        self.assertNotEqual(view.selected, target)
        self.assertEqual(picker_ui.handle(view, ("text", "y"), 0), "close")
        self.assertEqual(view.closing, target)
        picker_ui.handle(view, ("key", "ctrl-x"), 0)
        self.assertIsNone(picker_ui.handle(view, ("text", "n"), 0), "any other key cancels")
        self.assertIsNone(view.confirm)

    def test_commands_mode_keys(self):
        entries = sorted(picker.entries(snapshot()), key=lambda e: picker.rank(e, {})) + list(catalog().values())
        view = picker_ui.View()
        view.scope = 1
        view.results(1, entries)
        picker_ui.handle(view, ("text", ">ren"), 0)
        self.assertEqual((view.scope, view.query), (picker_ui.COMMANDS, "ren"), "> is the prompt, not the query")
        view.results(1, entries)
        self.assertEqual(view.current().entry["title"], "Rename workspace…")
        self.assertIsNone(picker_ui.handle(view, ("key", "enter"), 0))
        self.assertEqual((view.query, view.cursor), ("Project Alpha", 13), "the input starts from the old name")
        picker_ui.handle(view, ("key", "ctrl-u"), 0)
        picker_ui.handle(view, ("text", "Beta"), 0)
        self.assertEqual(view.current().entry["title"], "Rename workspace…", "typing a name doesn't re-rank")
        picker_ui.handle(view, ("key", "esc"), 0)
        self.assertEqual((view.prompt, view.query), (None, "ren"), "Esc goes back to the search")
        picker_ui.handle(view, ("key", "enter"), 0)
        picker_ui.handle(view, ("text", "x"), 0)
        self.assertEqual(picker_ui.handle(view, ("key", "enter"), 0), "run")
        self.assertEqual(view.running, view.prompt)
        view.unask()
        view.edit("ctrl-u")
        view.results(1, entries)
        view.edit(None, "close tab")
        view.results(1, entries)
        self.assertIsNone(picker_ui.handle(view, ("key", "enter"), 0), "destructive commands ask first")
        self.assertEqual(picker_ui.handle(view, ("text", "y"), 0), "run")
        view.edit("ctrl-u")
        picker_ui.handle(view, ("key", "backspace"), 0)
        self.assertEqual(view.scope, 1, "Backspace on an empty command search goes back")
        view.results(1, entries)
        self.assertTrue(all(i.entry["kind"] == "agent" for i, _ in view.rows))
        view.scope = 0
        view.results(1, entries)
        self.assertNotIn("command", {i.entry["kind"] for i, _ in view.rows}, "All lists panes only")

    def test_only_y_confirms(self):
        rows = Rows(picker.entries(snapshot()))
        view, _ = rows.view()
        for key in (("key", "enter"), ("key", "ctrl-x"), ("text", "n")):
            picker_ui.handle(view, ("key", "ctrl-x"), 0)
            self.assertIsNone(picker_ui.handle(view, key, 0), key)
            self.assertIsNone(view.confirm)
        picker_ui.handle(view, ("key", "ctrl-x"), 0)
        self.assertEqual(picker_ui.handle(view, ("text", "Y"), 0), "close")

    def test_double_click_does_what_enter_does(self):
        entries = Rows(picker.entries(snapshot()) + list(catalog().values())).entries
        view = picker_ui.View()
        view.scope = picker_ui.COMMANDS
        view.results(1, entries)
        view.hits = {"rows": {6: 1}, "tabs": [], "tabs_y": 3, "preview": None}
        picker_ui.handle(view, ("mouse", (0, 5, 6, True)), 1.0)
        self.assertIsNone(picker_ui.handle(view, ("mouse", (0, 5, 6, True)), 1.2))
        self.assertEqual(view.prompt, view.selected, "a command asks for its name, never tries to focus")

    def test_selection_change_resets_the_preview_scroll(self):
        rows = Rows(picker.entries(snapshot()))
        view, _ = rows.view()
        view.preview_scroll = 9
        view.edit(None, "nvim")
        view.results(1, rows.entries)
        self.assertEqual(view.preview_scroll, 0)

    def test_dim_is_the_terminal_text_dimmed(self):
        palette = picker_ui.Palette(theme.load({}))
        self.assertEqual(palette("overlay1", "sidebar_bg"), Style(None, None, False, True, False))
        self.assertEqual(palette("text", "surface0"), Style(None, None, False, False, False), "never a dim background")

    def test_line_editing_follows_readline(self):
        view = picker_ui.View()
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

    def test_spinner_and_status_icons(self):
        working = picker.entries(snapshot())[1]
        self.assertNotEqual(picker_ui.status_icon(working, 0)[0], picker_ui.status_icon(working, .1)[0])
        self.assertEqual(picker_ui.status_icon(working, 0, symbols=True), ("◐", "yellow"))
        self.assertEqual(picker_ui.status_icon(picker.entries(snapshot())[0], 0), ("●", "red"))

    def test_theme_follows_herdr_tokens_and_overrides(self):
        tokens = theme.load({"theme": {"name": "tokyo-night", "custom": {"red": "#123", "panel_bg": "reset"}},
                             "ui": {"accent": "magenta", "status_indicators": "symbols"}})
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
        self.assertLess(int(light["panel_bg"][1:3], 16), 0xf9, "light terminals lift toward the text color")
        passive = theme.load({})
        self.assertIsNone(passive["panel_bg"])
        self.assertFalse(passive["lifted"])
        self.assertEqual(theme.load({"ui": {"accent": "cyan"}}, ("#ffffff", "#000000"))["accent"], 6)
        self.assertEqual(theme.load({"theme": {"name": "nord"}}, ("#ffffff", "#000000"))["panel_bg"], "#2e3440")
        self.assertEqual(theme.load({"theme": {"name": "terminal"}}, ("#ffffff", "#000000"))["panel_bg"], "#121212")

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


class TerminalTests(unittest.TestCase):
    def test_input_decoding(self):
        cases = {"\x1b[A": ("key", "up"), "\x1b[1;2B": ("key", "shift-down"), "\x1b[1;5D": ("key", "ctrl-left"),
                 "\x1bOA": ("key", "up"), "\x1b[3~": ("key", "delete"), "\x1b[6~": ("key", "pgdn"),
                 "\x1b[Z": ("key", "shift-tab"), "\x1bb": ("key", "alt-b"), "\x1b\x7f": ("key", "alt-backspace"),
                 "\r": ("key", "enter"), "\x0e": ("key", "ctrl-n"), "\x19": ("key", "ctrl-y"),
                 "\x1f": ("key", "ctrl-/"),
                 "\x7f": ("key", "backspace"), "\t": ("key", "tab"), "\x1b[99;5u": ("key", "ctrl-c"),
                 "héllo": ("text", "héllo"), "\x1b[<0;5;3M": ("mouse", (0, 4, 2, True)),
                 "\x1b[<65;1;1M": ("mouse", (65, 0, 0, True)), "\x1b[200~a\nb\x1b[201~": ("paste", "a\nb")}
        for sequence, expected in cases.items():
            self.assertEqual(parse_input(sequence), (expected, len(sequence)), repr(sequence))
        self.assertEqual(parse_input("\x1b"), (None, 0))
        self.assertEqual(parse_input("\x1b", final=True), (("key", "esc"), 1))
        self.assertEqual(parse_input("\x1b[1;"), (None, 0))
        self.assertEqual(parse_input("\x1b[200~unfinished"), (None, 0))
        self.assertEqual(parse_input("abc\x1b[A"), (("text", "abc"), 3))

    def test_preview_decodes_colors_and_drops_other_controls(self):
        base = Style("#ffffff", "#000000")
        text = ("\x1b[38;2;1;2;3mrgb\x1b[0m \x1b[38;5;4midx\x1b[39m \x1b[91mbright\x1b[0m\r\n"
                "\x1b[7mrev\x1b[27m\x1b]8;;http://x\x07link\x1b]8;;\x07\tz\x1b[2K\n\x1b[38:2::9:8:7mcolon\n\n  \n")
        lines = parse_ansi(text, base)
        self.assertEqual(len(lines), 3, "trailing blank lines are trimmed")
        self.assertEqual([(t, s.fg) for t, s in lines[0]],
                         [("rgb", "#010203"), (" ", "#ffffff"), ("idx", 4), (" ", "#ffffff"), ("bright", 9)])
        self.assertEqual(lines[1][0], ("rev", Style("#000000", "#ffffff")))
        self.assertEqual("".join(t for t, _ in lines[1]), "revlink z", "tab stops count printed columns")
        self.assertEqual(lines[2][0][1].fg, "#090807")

    def test_color_query_replies_are_read_and_never_typed(self):
        self.assertEqual(rgb("b3b3", "b3b3", "b3b3"), "#b3b3b3")
        self.assertEqual(rgb("f", "0", "80"), "#ff0080")
        reply = "\x1b]11;rgb:0000/0000/0000\x1b\\"
        self.assertEqual(parse_input(reply + "x"), (None, len(reply)))
        self.assertEqual(parse_input("\x1b]11;rgb:00"), (None, 0))
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        import tty
        tty.setraw(slave)
        os.write(master, b"\x1b]10;rgb:b3b3/b3b3/b3b3\x07\x1b]11;rgb:0000/0000/0000\x1b\\\x1b[?62;22cj")
        screen = Terminal(stream, stream)
        self.assertEqual(screen.colors(), ("#b3b3b3", "#000000"))
        self.assertEqual(screen.pending, "j", "a key typed during the query stays queued")
        self.assertIn(b"\x1b]11;?", os.read(master, 1024))
        started = time.monotonic()
        self.assertIsNone(Terminal(stream, stream).colors(timeout=0.2))
        self.assertLess(time.monotonic() - started, 1)

    def test_refresh_rewrites_only_what_changed_and_wake_ends_the_wait(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (3, 20))
        screen = Terminal(stream, stream)
        screen.put(0, 0, "⠋ working 你好", Style())
        screen.refresh()
        os.read(master, 4096)
        screen.cells = [list(row) for row in screen.cells]
        screen.put(0, 0, "⠙", Style())
        screen.refresh()
        self.assertEqual(os.read(master, 4096), b"\x1b[?25l\x1b[1;1H\x1b[0;39;49m\xe2\xa0\x99", "one cell, not the row")
        screen.cells = [list(row) for row in screen.cells]
        screen.put(0, 11, "x", Style())
        screen.refresh()
        self.assertIn(b"\x1b[1;11H", os.read(master, 4096), "overwriting half a wide glyph rewrites it whole")
        started = time.monotonic()
        threading.Timer(0.05, screen.wake).start()
        self.assertIsNone(screen.event(5))
        self.assertLess(time.monotonic() - started, 1, "wake() ends a long wait")

    def test_keys_typed_before_the_popup_starts_are_kept(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (5, 20))
        os.write(master, b"cla")
        time.sleep(0.05)
        def drain():  # Leaving the terminal waits for its output to be read, as a real terminal would.
            try:
                while os.read(master, 65536):
                    pass
            except OSError:
                pass  # The test closed the terminal.
        threading.Thread(target=drain, daemon=True).start()
        with Terminal(stream, stream) as screen:
            self.assertEqual(screen.event(0.5), ("text", "cla"))

    def test_drawing_into_the_last_frame_still_repaints(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (8, 40))
        screen = Terminal(stream, stream)
        screen.refresh()
        os.read(master, 4096)
        picker_ui.draw_message(screen, picker_ui.Palette(theme.load({})), [("Select Box in the sidebar", "text")])
        screen.refresh()
        self.assertIn(b"Select Box", os.read(master, 4096), "the remote-focus message must show")

    def test_a_paste_without_its_end_is_delivered(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (5, 20))
        import tty
        tty.setraw(slave)
        screen = Terminal(stream, stream)
        os.write(master, b"\x1b[200~half a paste")
        deadline, event = time.monotonic() + 3, None
        while not event and time.monotonic() < deadline:
            event = screen.event(0.2)
        self.assertEqual(event, ("paste", "half a paste"))

    def test_style_output_and_wide_glyph_cells(self):
        self.assertEqual(sgr(Style("#89b4fa", None, True)), "\x1b[0;1;38;2;137;180;250;49m")
        self.assertEqual(sgr(Style(4, 9)), "\x1b[0;34;101m")
        self.assertEqual(sgr(Style(200, None)), "\x1b[0;38;5;200;49m")
        frame = Canvas(2, 6)
        frame.put(0, 0, "你a", Style())
        self.assertEqual([char for char, _ in frame.cells[0]][:3], ["你", "", "a"])
        frame.put(0, 1, "b", Style())
        self.assertEqual([char for char, _ in frame.cells[0]][:3], [" ", "b", "a"], "no orphaned half glyphs")
        frame.put(1, 0, "你\u0301❤\ufe0fx", Style())
        self.assertEqual([char for char, _ in frame.cells[1]][:4], ["你\u0301", "", "❤\ufe0f", "x"],
                         "marks and emoji selectors stay with their glyph")


class DrawTests(unittest.TestCase):
    def draw(self, height, width, entries=None, read=lambda entry, lines: PREVIEW, colors=None, wall=None, **state):
        rows = Rows(entries or picker.entries(snapshot()))
        view, results = rows.view(**state)
        palette = picker_ui.Palette(theme.load({}, colors))
        frame = Canvas(height, width)
        with picker_ui.Previewer(Style(), read) as previewer:
            for _ in range(20):
                picker_ui.draw(frame, palette, view, rows.entries, view.results(1, rows.entries), 0, previewer,
                               wall=wall)
                if not view.preview or previewer.get(view.selected):
                    break
                time.sleep(0.02)
            picker_ui.draw(frame, palette, view, rows.entries, view.results(1, rows.entries), 0, previewer, wall=wall)
        return frame, view

    def test_layout_keeps_selection_visible_and_fits_every_size(self):
        base = picker.entries(snapshot())
        entries = [{**base[i % 5], "id": str(i), "key": str(i), "title": f"Task {i} 中文", "order": [i]}
                   for i in range(40)]
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
        entries = picker.entries(snapshot()) + [picker.error_entry("Build box", "ssh: connection refused", "bb")]
        frame, view = self.draw(30, 150, entries, selected=entries[3]["id"], confirm=entries[3]["id"])
        text = frame.text()
        self.assertIn("Build box  ssh: connection refused", text)
        self.assertIn("Close terminal in Project Alpha:dev?", text)

    def test_mouse_selects_scopes_and_double_click_accepts(self):
        frame, view = self.draw(30, 150)
        row = next(y for y, index in view.hits["rows"].items() if index == 2)
        self.assertIsNone(picker_ui.handle(view, ("mouse", (0, 10, row, True)), 1.0))
        self.assertEqual(view.index, 2)
        self.assertEqual(picker_ui.handle(view, ("mouse", (0, 10, row, True)), 1.2), "accept")
        start, _, _ = view.hits["tabs"][2]
        picker_ui.handle(view, ("mouse", (0, start + 1, view.hits["tabs_y"], True)), 2.0)
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
        entries = picker.entries(snapshot())
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
        entries = picker.entries(snapshot()) + list(catalog(keys=keys).values())
        frame, view = self.draw(30, 150, entries, scope=picker_ui.COMMANDS, query="close tab", cursor=9)
        text = frame.text()
        self.assertIn("> close tab", text)
        self.assertRegex(text, r"× Close tab +tab +Project Alpha:dev +⌃S &")
        self.assertIn("Closes tab dev in Project Alpha and everything running in it:", text)
        self.assertIn("› nvim  ~/projects/alpha", text, "the preview lists what will stop")
        frame, view = self.draw(30, 150, entries, scope=picker_ui.COMMANDS)
        self.assertIn("> run a command", frame.text())

    def test_a_long_old_name_never_hides_what_you_type(self):
        entries = picker.entries(snapshot()) + list(catalog().values())
        for entry in entries:
            if entry["kind"] == "command" and entry.get("was"):
                entry["was"] = entry["ask"] = "a-very-long-workspace-name-indeed"
        for width in (120, 40):
            view = Rows(entries).view(scope=picker_ui.COMMANDS)[0]
            view.results(1, Rows(entries).entries)
            view.ask(view.current().entry)
            view.edit("ctrl-u")
            view.edit(None, "typed")
            palette = picker_ui.Palette(theme.load({}))
            frame = Canvas(24, width)
            with picker_ui.Previewer(Style(), lambda entry, lines: "") as previewer:
                picker_ui.draw(frame, palette, view, Rows(entries).entries, view.results(1, Rows(entries).entries), 0,
                               previewer)
            self.assertIn("typed", frame.text(), width)

    def test_empty_tabs_and_searches_say_so(self):
        terminals = [e for e in picker.entries(snapshot()) if e["kind"] == "terminal"]
        frame, _ = self.draw(30, 150, terminals, scope=1)
        self.assertIn("No agents running", frame.text())
        frame, _ = self.draw(30, 150, query="zzz")
        self.assertIn("No matches for “zzz”", frame.text())


class InteractiveTests(unittest.TestCase):
    """Drive the real terminal UI through a pseudo-terminal."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def run_picker(self, keys, size=(32, 160), resize=None, focus_error=None, answer=None):
        output = Path(self.temp.name) / "result.json"
        pid, fd = pty.fork()
        if pid == 0:
            try:
                termios.tcsetwinsize(0, size)
                os.environ.update(TERM="xterm-256color", HERDR_CONFIG_PATH="/nonexistent")
                picker.STATE = Path(self.temp.name)

                def rpc(method, params=None):
                    with open(Path(self.temp.name) / "rpc.log", "a") as log:
                        log.write(json.dumps([method, params]) + "\n")
                    return {"snapshot": snapshot(), "actions": []}
                picker.rpc = rpc
                picker.cli = lambda *args, **kwargs: []
                picker.read = lambda entry, lines: PREVIEW

                def focus(entry):
                    if focus_error:
                        raise RuntimeError(focus_error)
                    output.write_text(json.dumps({"focused": entry}))
                picker.focus = focus
                picker.close = lambda entry: output.write_text(json.dumps({"closed": entry}))
                picker.picker()
                os._exit(0)
            except BaseException:
                import traceback
                traceback.print_exc()
                os._exit(1)
        transcript = b""

        answered = False

        def drain(seconds):
            nonlocal transcript, answered
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        transcript += os.read(fd, 65536)
                    except OSError:
                        return
                if answer and not answered and b"\x1b]11;?" in transcript:
                    os.write(fd, answer)
                    answered = True
        try:
            deadline = time.monotonic() + 8
            while b"Awaiting approval" not in transcript and time.monotonic() < deadline:
                drain(0.1)
            self.assertIn(b"Awaiting approval", transcript)
            drain(0.3)
            if resize:
                termios.tcsetwinsize(fd, resize)
                drain(0.3)
            for chunk in keys:
                os.write(fd, chunk)
                drain(0.25)
            while time.monotonic() < deadline:
                finished, status = os.waitpid(pid, os.WNOHANG)
                if finished:
                    pid = None
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, transcript.decode(errors="replace"))
                    break
                drain(0.1)
            self.assertIsNone(pid, transcript.decode(errors="replace")[-3000:])
            self.assertTrue(termios.tcgetattr(fd)[3] & termios.ICANON, "terminal modes restored")
            return (json.loads(output.read_text()) if output.exists() else None), transcript
        finally:
            os.close(fd)
            if pid:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)

    def test_ctrl_n_p_y_focuses_and_preview_streams(self):
        result, transcript = self.run_picker([b"\x0e\x0e\x10", b"\x19"])
        self.assertEqual(result["focused"]["status"], "working")
        self.assertIn(b"Preview", transcript)
        self.assertIn(b"38;2;137;180;250", transcript)

    def test_colors_follow_the_terminal_that_answers(self):
        reply = b"\x1b]10;rgb:b3b3/b3b3/b3b3\x1b\\\x1b]11;rgb:0000/0000/0000\x1b\\\x1b[?62;22c"
        _, transcript = self.run_picker([b"\x1b"], answer=reply)
        self.assertIn(b"48;2;13;13;13", transcript, "the preview is lifted from the reported background")
        _, silent = self.run_picker([b"\x1b"])
        self.assertNotIn(b"48;2;13;13;13", silent)

    def test_command_runs_from_the_prompt(self):
        result, transcript = self.run_picker([b">rename tab", b"\r", b"\x15", b"Shipping", b"\r"])
        calls = [json.loads(line) for line in (Path(self.temp.name) / "rpc.log").read_text().splitlines()]
        self.assertIn(["tab.rename", {"tab_id": "w1:t2", "label": "Shipping"}], calls)
        self.assertIn("Rename tab".encode(), transcript)

    def test_typed_query_then_enter(self):
        result, _ = self.run_picker([b"nvim", b"\r"])
        self.assertEqual(result["focused"]["pane_id"], "w1:p7")

    def test_escape_cancels_without_selection(self):
        result, _ = self.run_picker([b"\x1b"])
        self.assertIsNone(result)

    def test_arrow_keys_after_resize(self):
        result, _ = self.run_picker([b"\x1b[B", b"\r"], resize=(18, 62))
        self.assertEqual(result["focused"]["status"], "working")

    def test_bracketed_multiline_paste(self):
        result, _ = self.run_picker([b"\x1b[200~Writing\nimplementation\x1b[201~", b"\x19"])
        self.assertEqual(result["focused"]["status"], "working")

    def test_close_requires_confirmation(self):
        result, transcript = self.run_picker([b"\x18", b"n", b"\x18", b"y", b"\x1b"])
        self.assertEqual(result["closed"]["status"], "blocked")
        self.assertIn(b"Close opencode in Project Alpha:1?", transcript)

    def test_focus_failure_stays_open_with_message(self):
        result, transcript = self.run_picker([b"\r", b"\x1b"], focus_error="That pane was replaced")
        self.assertIsNone(result)
        self.assertIn(b"That pane was replaced", transcript)


if __name__ == "__main__":
    unittest.main()
