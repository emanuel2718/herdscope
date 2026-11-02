"""Herdr's side of the plugin: the environment Herdr runs it in, its socket API and its CLI."""

import json
import os
import re
import signal
import socket
import time
from pathlib import Path

PLUGIN = os.environ.get("HERDR_PLUGIN_ID", "emanuel2718.herdscope")
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
SOCKET = os.environ.get("HERDR_SOCKET_PATH", str(Path.home() / ".config/herdr/herdr.sock"))
STATE = Path(
    os.environ.get("HERDR_PLUGIN_STATE_DIR")
    or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "herdr/plugins" / PLUGIN
)


def state():
    """The plugin's state folder, made if needed and kept private: it records which panes you
    visit, and what agents on other machines are working on."""
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        STATE.chmod(0o700)
    except OSError:
        pass  # Not ours to change; what's in it still works.
    return STATE


# Failures worth a message rather than a traceback.
ERRORS = (OSError, ValueError, KeyError, RuntimeError)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def printable(char):
    """Whether `char` draws as a glyph: what str.isprintable() accepts, and also private-use
    characters (Nerd Font and powerline icons) and ones newer than Python's Unicode tables."""
    if char.isprintable():
        return True
    # Imported here: only rare characters get this far, and the hooks start faster without it.
    import unicodedata  # noqa: PLC0415

    return unicodedata.category(char) in ("Co", "Cn")


def clean(value):
    """`value` as one printable line: escapes and control characters removed, whitespace collapsed."""
    text = str(value or "")
    if not text.isprintable():  # Rare, so most labels skip the per-character pass.
        text = "".join(c for c in ANSI.sub("", text) if printable(c) or c.isspace())
    return " ".join(text.split())


def rpc(method, params=None):
    """One request over Herdr's socket; raises RuntimeError with Herdr's message on failure."""
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(3)
        sock.connect(SOCKET)
        sock.sendall((json.dumps({"id": "herdscope", "method": method, "params": params or {}}) + "\n").encode())
        with sock.makefile("r") as stream:
            reply = json.loads(stream.readline())
    if "error" in reply:
        raise RuntimeError(reply["error"]["message"])
    return reply["result"]


def cli(args, stop=None, timeout=6, raw=False):
    """Run `herdr *args`, for what the socket can't do, such as reaching other machines.

    Setting `stop` or passing `timeout` kills the whole process group, SSH included.
    """
    # Imported here: the hooks never reach other machines, and start faster without it.
    import subprocess  # noqa: PLC0415

    process = subprocess.Popen(
        [HERDR, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            if (stop and stop.is_set()) or time.monotonic() >= deadline:
                raise RuntimeError("Connection cancelled or timed out")
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            # Herdr's CLI reports failures as JSON on stderr; show just the message.
            text = (stderr or stdout).strip()
            try:
                message = json.loads(text)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = text
            raise RuntimeError(clean(message)[:500])
        if raw:
            return stdout
        reply = json.loads(stdout)
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(reply["error"].get("message", str(reply["error"])))
        return reply.get("result", reply) if isinstance(reply, dict) else reply
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()


def spawn(args):
    """Start `args` on its own, to finish after the popup closes; it must not outlive Herdr's patience.

    Used for short, bounded follow-ups only: an action that needs the screen once the popup is
    gone, or an agent start Herdr waits on for at most its own timeout.
    """
    import subprocess  # noqa: PLC0415 - as in cli(): only running a command needs it.

    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
