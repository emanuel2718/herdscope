"""The plugin as Herdr sees it: the manifest and the shell scripts it runs."""

import os
import subprocess
from pathlib import Path

from tests.support import ROOT, StateTestCase


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
