"""Fixtures shared by the tests: a small Herdr snapshot and stand-ins for the picker's parts."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdscope import commands, herdr, inventory, view

ROOT = Path(__file__).resolve().parent.parent
PREVIEW = "\x1b[38;2;137;180;250mPreview\x1b[0m line one\r\n\x1b[1;31mfailure\x1b[0m\r\n"


def snapshot():
    """One workspace: three opencode agents in tab 1, and nvim and a shell in tab "dev"."""
    statuses = [("blocked", "Awaiting approval"), ("working", "Writing implementation"), ("idle", "Ready for a task")]
    panes = [
        {
            "pane_id": f"w1:p{i}",
            "terminal_id": f"term-{i}",
            "workspace_id": "w1",
            "tab_id": "w1:t1",
            "agent": "opencode",
            "agent_status": status,
            "terminal_title_stripped": title,
        }
        for i, (status, title) in enumerate(statuses)
    ]
    panes += [
        {
            "pane_id": "w1:p7",
            "terminal_id": "term-7",
            "workspace_id": "w1",
            "tab_id": "w1:t2",
            "terminal_title_stripped": "nvim ~/p/alpha",
            "cwd": str(Path.home() / "projects/alpha"),
        },
        {
            "pane_id": "w1:p8",
            "terminal_id": "term-8",
            "workspace_id": "w1",
            "tab_id": "w1:t2",
            "terminal_title_stripped": "~/p/alpha",
            "cwd": str(Path.home() / "projects/alpha"),
        },
    ]
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
        self.entries = sorted((e for e in entries if e["kind"] != "command"), key=lambda e: inventory.rank(e, {})) + [
            e for e in entries if e["kind"] == "command"
        ]

    def view(self, **state):
        shown = view.View()
        results = shown.results(1, self.entries)
        for key, value in state.items():
            setattr(shown, key, value)
        return shown, results


def catalog(actions=(), agents=(), keys=None, workspaces=1):
    """Commands for the snapshot fixture, opened from the ~/projects/alpha shell in tab "dev"."""
    data = snapshot()
    data["workspaces"] += [
        {"workspace_id": f"w{i}", "label": f"Space {i}", "number": i} for i in range(2, workspaces + 1)
    ]
    with patch.object(commands.shutil, "which", side_effect=lambda name: name in agents):
        return {c["id"].split(":", 1)[1]: c for c in commands.catalog(data, {"keys": keys or {}}, actions)}


class StateTestCase(unittest.TestCase):
    """A test with its own plugin state folder and Herdr socket path, so history starts empty."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("STATE", Path(self.temp.name)), ("SOCKET", f"{self.temp.name}/herdr.sock")):
            patcher = patch.object(herdr, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
