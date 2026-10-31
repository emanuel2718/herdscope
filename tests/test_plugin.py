"""The plugin as Herdr sees it: the manifest and the shell scripts it runs."""

import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

from herdscope import __main__
from tests.support import ROOT, StateTestCase

SEMVER = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$"
# The events the manifest hooks into, as Herdr names them.
EVENTS = {"pane.focused", "pane.agent_status_changed"}


class ScriptTests(StateTestCase):
    def test_open_action_asks_herdr_and_tolerates_a_second_press(self):
        fake = Path(self.temp.name) / "herdr"
        for reply, code, expected in (
            ("{}", 0, 0),
            ('{"error":{"message":"a popup pane is already open"}}', 1, 0),
            ('{"error":{"message":"plugin not found"}}', 1, 1),
        ):
            # Like Herdr's CLI: errors go to stderr.
            fake.write_text(f"#!/bin/sh\necho \"$*\" > {fake}.args\necho '{reply}' >&{2 if code else 1}\nexit {code}\n")
            fake.chmod(0o755)
            result = subprocess.run(
                ["sh", "scripts/open.sh"],
                env={**os.environ, "HERDR_BIN_PATH": str(fake)},
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(result.returncode, expected, reply)
            self.assertEqual(Path(f"{fake}.args").read_text().split()[:3], ["plugin", "pane", "open"])
        self.assertIn("plugin not found", result.stderr)


class ManifestTests(unittest.TestCase):
    """What Herdr checks when it installs the plugin, and the marketplace when it lists it."""

    def setUp(self):
        self.manifest = tomllib.loads((ROOT / "herdr-plugin.toml").read_text())

    def test_metadata_is_complete_and_well_formed(self):
        manifest = self.manifest
        for field in ("id", "name", "version", "min_herdr_version", "description", "platforms"):
            self.assertTrue(manifest.get(field), field)
        self.assertRegex(manifest["id"], r"^[A-Za-z0-9.:_-]+$")
        self.assertRegex(manifest["version"], SEMVER)
        self.assertRegex(manifest["min_herdr_version"], SEMVER)
        self.assertLessEqual(set(manifest["platforms"]), {"linux", "macos", "windows"})
        for table in ("actions", "panes"):
            ids = [item["id"] for item in manifest[table]]
            self.assertEqual(len(ids), len(set(ids)), f"{table} ids are unique")
            for local in ids:
                self.assertRegex(local, r"^[A-Za-z0-9:_-]+$", "local ids may not contain dots")
        self.assertLessEqual({event["on"] for event in manifest["events"]}, EVENTS)

    def test_every_command_runs_something_in_the_plugin(self):
        commands = [item["command"] for table in ("actions", "panes", "events") for item in self.manifest[table]]
        for command in commands:
            if command[0] == "sh":
                self.assertTrue((ROOT / command[1]).is_file(), command)
            else:
                self.assertEqual(command[:3], ["python3", "-m", "herdscope"], command)
                self.assertIn(command[3], __main__.ACTIONS, command)

    def test_the_plugin_id_is_the_same_everywhere(self):
        plugin = self.manifest["id"]
        self.assertIn(f'"HERDR_PLUGIN_ID", "{plugin}"', (ROOT / "herdscope/herdr.py").read_text())
        self.assertIn(f"plugin=${{HERDR_PLUGIN_ID:-{plugin}}}", (ROOT / "scripts/open.sh").read_text())
        self.assertIn(f'command = "{plugin}.open"', (ROOT / "README.md").read_text(), "the key binding to copy")

    def test_the_changelog_describes_this_version(self):
        changelog = (ROOT / "CHANGELOG.md").read_text()
        released = re.findall(r"^## \[(\d[^\]]*)\]", changelog, re.MULTILINE)
        self.assertEqual(released[0], self.manifest["version"], "the newest release in CHANGELOG.md")
