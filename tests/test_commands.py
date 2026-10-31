"""The Commands tab's catalog: what each command targets, shows and calls."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdscope import commands, herdr
from tests.support import catalog


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
        found = catalog(
            actions=[
                {"plugin_id": "someone.notes", "action_id": "jot", "title": "Jot a note"},
                {"plugin_id": herdr.PLUGIN, "action_id": "open", "title": "Herdscope"},
            ],
            agents=("claude",),
        )
        self.assertEqual(found["rename_workspace"]["target"], "Project Alpha")
        self.assertEqual(found["rename_tab"]["target"], "Project Alpha:dev")
        self.assertEqual(found["split_right"]["target"], "~/projects/alpha")
        self.assertEqual(found["close_tab"]["confirm"], "Close tab dev in Project Alpha? Its 2 panes stop.")
        self.assertEqual(len(found["close_tab"]["affects"]), 2)
        self.assertIn("start_claude", found)
        self.assertNotIn("start_codex", found, "only agents that are installed")
        self.assertIn("plugin:someone.notes:jot", found)
        self.assertNotIn(f"plugin:{herdr.PLUGIN}:open", found, "never itself")
        self.assertNotIn("move_workspace_up", found, "a single workspace has nowhere to move")
        self.assertNotIn("new_worktree", found, "not a Git checkout")
        self.assertEqual(found["rename_workspace"]["title"], "Rename workspace…", "… marks commands that ask")

    def test_each_command_makes_the_right_call(self):
        found = catalog(agents=("claude",), workspaces=3)
        calls = []

        def rpc(method, params=None):
            calls.append((method, params))
            return {"root_pane": {"pane_id": "w1:p9"}}

        with patch.object(herdr, "rpc", rpc), patch.object(herdr, "spawn") as spawn:
            found["rename_workspace"]["run"]("  Renamed  ")
            found["split_right"]["run"](None)
            found["close_tab"]["run"](None)
            found["move_workspace_down"]["run"](None)
            found["rename_pane"]["run"]("")
            found["start_claude"]["run"](None)
            with self.assertRaisesRegex(RuntimeError, "Type a name first"):
                found["rename_tab"]["run"]("   ")
        self.assertEqual(
            calls[:5],
            [
                ("workspace.rename", {"workspace_id": "w1", "label": "Renamed"}),
                (
                    "pane.split",
                    {
                        "target_pane_id": "w1:p8",
                        "direction": "right",
                        "focus": True,
                        "cwd": str(Path.home() / "projects/alpha"),
                    },
                ),
                ("tab.close", {"tab_id": "w1:t2"}),
                ("workspace.move_block", {"workspace_ids": ["w1"], "before_workspace_id": "w3"}),
                ("pane.rename", {"pane_id": "w1:p8", "label": None}),
            ],
        )
        self.assertEqual(calls[5][0], "tab.create")
        self.assertEqual(
            spawn.call_args.args[0][3:],
            [herdr.HERDR, "claude-w1-p9", "claude", "w1:p9"],
            "a name no other agent has: the new pane's",
        )

    def test_spawned_commands_outlive_the_popup_without_its_terminal(self):
        with patch("subprocess.Popen") as popen:
            herdr.spawn(["herdr", "plugin", "action", "invoke", "x"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"], "closing the popup doesn't stop it")
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL, "the popup's terminal is gone")

    def test_agents_are_offered_only_when_installed(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / "claude").write_text("#!/bin/sh\n")
        (folder / "claude").chmod(0o755)
        (folder / "codex").write_text("not executable")
        (folder / "gemini").mkdir()
        with patch.dict(os.environ, {"PATH": f"/nonexistent{os.pathsep}{folder}"}):
            self.assertTrue(commands.installed("claude"))
            self.assertFalse(commands.installed("codex"), "not executable")
            self.assertFalse(commands.installed("gemini"), "a folder")
            self.assertFalse(commands.installed("opencode"))

    def test_agent_start_retries_then_reports_a_failure(self):
        fake = Path(tempfile.mkdtemp()) / "herdr"
        self.addCleanup(shutil.rmtree, fake.parent)
        fake.write_text(f'#!/bin/sh\necho "$*" >> {fake}.calls\ncase $1 in agent) exit 1;; esac\n')
        fake.chmod(0o755)
        subprocess.run(["sh", "-c", commands.START, str(fake), "claude-w1-p9", "claude", "w1:p9"], check=True)
        calls = Path(f"{fake}.calls").read_text().splitlines()
        self.assertEqual(calls[:2], ["agent start claude-w1-p9 --kind claude --pane w1:p9"] * 2)
        self.assertEqual(calls[2], "notification show Herdscope --body Couldn't start claude in the new tab")

    def test_other_plugins_run_after_the_popup_and_wording_fits(self):
        found = catalog(actions=[{"plugin_id": "someone.notes", "action_id": "jot", "title": "Jot"}])
        with patch.object(herdr, "spawn") as spawn:
            found["plugin:someone.notes:jot"]["run"](None)
        self.assertEqual(spawn.call_args.args[0][:2], ["sh", "-c"])
        self.assertIn("sleep", spawn.call_args.args[0][2], "after the popup closes, so its own popup can open")
        self.assertEqual(spawn.call_args.args[0][-4:], ["plugin", "action", "invoke", "someone.notes.jot"])
        self.assertEqual(found["close_workspace"]["confirm"], "Close workspace Project Alpha? Its 5 panes stop.")
        self.assertEqual(found["rename_pane"]["ask"], "~/projects/alpha", "the input starts from the current name")
        self.assertIsNone(found["rename_tab"]["blank"], "a tab needs a name")
        self.assertEqual(found["rename_pane"]["blank"], "its own title")
