"""End to end: the real picker in a pseudo-terminal, against a stand-in Herdr."""

import json
import os
import pty
import select
import signal
import tempfile
import termios
import time
import unittest
from pathlib import Path

from herdscope import app, herdr, inventory
from tests.support import PREVIEW, snapshot


class AppTests(unittest.TestCase):

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
                herdr.STATE = Path(self.temp.name)

                def rpc(method, params=None):
                    with open(Path(self.temp.name) / "rpc.log", "a") as log:
                        log.write(json.dumps([method, params]) + "\n")
                    return {"snapshot": snapshot(), "actions": []}
                herdr.rpc = rpc
                herdr.cli = lambda *args, **kwargs: []
                inventory.read = lambda entry, lines: PREVIEW

                def focus(entry):
                    if focus_error:
                        raise RuntimeError(focus_error)
                    output.write_text(json.dumps({"focused": entry}))
                inventory.focus = focus
                inventory.close = lambda entry: output.write_text(json.dumps({"closed": entry}))
                app.main()
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
        self.assertIn(b"Rename tab", transcript)

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
