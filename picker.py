#!/usr/bin/env python3
"""Herdscope's Herdr side: inventory, navigation, and visit and status history.

Presentation lives in picker_ui, Herdr commands in commands.
"""

import json
import math
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time

from contextlib import contextmanager
from pathlib import Path

PLUGIN = os.environ.get("HERDR_PLUGIN_ID", "emanuel2718.herdscope")
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
SOCKET = os.environ.get("HERDR_SOCKET_PATH", str(Path.home() / ".config/herdr/herdr.sock"))
STATE = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or Path(
    os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "herdr/plugins" / PLUGIN)

# The pane the popup was opened from
ORIGIN = os.environ.get("HERDSCOPE_PANE")
ERRORS = (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error)
HALF_LIFE = 7 * 86400
FORGET = 90 * 86400  # Visits and statuses older than this are dropped.
STATUS_ORDER = {"blocked": 0, "working": 1, "done": 2, "idle": 3, "unknown": 4}
SECTION = {"agent": 0, "terminal": 1, "error": 2}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def clean(value):
    return " ".join("".join(c for c in ANSI.sub("", str(value or "")) if c.isprintable() or c.isspace()).split())


def rpc(method, params=None):
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
    # Kill the complete forwarding process group on cancellation/timeout, including SSH.
    process = subprocess.Popen([HERDR, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
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


@contextmanager
def database():
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE / "history.sqlite3", timeout=5)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS visits (key TEXT PRIMARY KEY, weight REAL, last REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS statuses (key TEXT PRIMARY KEY, status TEXT, since REAL)")
            yield db
    finally:
        db.close()


def decay(weight, last, now):
    return weight * math.exp2(-max(0, now - last) / HALF_LIFE)


def record(key, now=None):
    now = time.time() if now is None else now
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT weight, last FROM visits WHERE key = ?", (key,)).fetchone()
        # The picker and pane.focused hook can observe the same visit.
        if old and now - old[1] < 3:
            return
        weight = decay(*old, now) + 1 if old else 1
        db.execute("INSERT OR REPLACE INTO visits VALUES (?, ?, ?)", (key, weight, now))
        db.execute("DELETE FROM visits WHERE last < ?", (now - FORGET,))


def history():
    """Visit history as {key: (decayed score, last visit time)}."""
    with database() as db:
        now = time.time()
        return {key: (decay(weight, last, now), last) for key, weight, last in db.execute("SELECT * FROM visits")}


def record_status(key, status, now=None):
    """Remember when an agent entered `status`; repeats of the same status keep the first time."""
    now = time.time() if now is None else now
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT status FROM statuses WHERE key = ?", (key,)).fetchone()
        if not old or old[0] != status:
            db.execute("INSERT OR REPLACE INTO statuses VALUES (?, ?, ?)", (key, status, now))
            db.execute("DELETE FROM statuses WHERE since < ?", (now - FORGET,))


def statuses():
    """{key: (status, since)} as recorded by the agent-status hook."""
    with database() as db:
        return {key: (status, since) for key, status, since in db.execute("SELECT * FROM statuses")}


def stamp(values, recorded, seen, now=None):
    """Set each local agent's "since": when it entered its current status, or None if unknown.

    `recorded` comes from the hook; a change the picker sees itself before the hook lands is
    stamped with the time it was seen. `seen` tracks the last status observed per key.
    """
    now = time.time() if now is None else now
    for entry in values:
        if entry["kind"] != "agent":
            continue
        key, status = entry["key"], entry["status"]
        if key in recorded and recorded[key][0] == status:
            entry["since"] = recorded[key][1]
        elif key in seen and seen[key] != status:
            recorded[key] = (status, now)
            entry["since"] = now
        else:
            entry["since"] = None
        seen[key] = status
    return values


def display_path(entry):
    path = entry.get("cwd", "")
    home = str(Path.home())
    if not entry.get("machine_id") and (path == home or path.startswith(home + "/")):
        path = "~" + path[len(home):]
    return path


def labels(entry):
    """(label, detail) for a pane: the task for agents, the command and directory for terminals."""
    title = entry.get("title", "")
    if entry["kind"] == "agent":
        title = re.sub(r"^OC \| ", "", title)
        return title or entry["agent"].title(), ""
    path = display_path(entry)
    if not title or title.startswith(("~", "/")):
        # Shells title themselves with an abbreviated cwd; show the real one.
        return path or "shell", ""
    return re.split(r"\s+(?:~/|~$|/)", title, maxsplit=1)[0], path


def entry_key(scope, pane):
    # Terminal IDs survive pane moves; machine/session namespaces prevent cross-server collisions.
    return json.dumps([scope, pane.get("terminal_id") or pane["pane_id"]], separators=(",", ":"))


def entries(snapshot, machine=None, origin=None):
    machine_id = machine["id"] if machine else None
    scope = ["machine", machine_id, machine.get("session", "default")] if machine else ["socket", SOCKET]
    workspaces = {w["workspace_id"]: w for w in snapshot.get("workspaces", [])}
    tabs = {t["tab_id"]: t for t in snapshot.get("tabs", [])}
    agents = {a["pane_id"]: a for a in snapshot.get("agents", [])}
    focused = None if machine else origin or snapshot.get("focused_pane_id")
    result = []
    for position, raw in enumerate(snapshot.get("panes", [])):
        pane = {**raw, **agents.get(raw["pane_id"], {})}
        agent = pane.get("agent")
        if machine and not agent:
            # The forwarded CLI has agent.focus, but no exact arbitrary-pane focus command.
            continue
        workspace = workspaces.get(pane["workspace_id"], {})
        tab = tabs.get(pane["tab_id"], {})
        key = entry_key(scope, pane)
        result.append({
            "id": key, "key": key,
            "kind": "agent" if agent else "terminal", "machine_id": machine_id,
            "machine": clean(machine["label"]) if machine else "Local",
            "pane_id": pane["pane_id"], "terminal_id": pane.get("terminal_id"),
            "workspace_id": pane["workspace_id"], "tab_id": pane["tab_id"],
            "agent": agent or "", "status": (pane.get("agent_status") or "unknown") if agent else "",
            "workspace": clean(workspace.get("label", pane["workspace_id"])),
            "tab": clean(tab.get("label", pane["tab_id"].split(":")[-1])),
            "title": clean(pane.get("title") or pane.get("terminal_title_stripped") or pane.get("label")),
            "cwd": pane.get("foreground_cwd") or pane.get("cwd") or "",
            "current": pane["pane_id"] == focused,
            "order": [workspace.get("number", 0), tab.get("number", 0), position],
        })
    return result


def error_entry(label, message, machine_id=None, pending=False):
    return {"id": json.dumps(["error", machine_id or label]),
            "kind": "error", "key": None, "machine": clean(label), "machine_id": machine_id,
            "status": "connecting" if pending else "offline", "error": clean(message)}


def rank(entry, scores):
    # Attention order first (blocked, working, done, idle), then frecency, then
    # sidebar order. The pane you are already looking at sinks to the end.
    visits = scores.get(entry.get("key"), (0, 0))
    return (SECTION[entry["kind"]], entry.get("current", False), STATUS_ORDER.get(entry.get("status"), 4),
            -visits[0], entry.get("machine_id") or "", entry.get("order", []), entry["id"])


def validate(entry):
    if entry.get("error"):
        raise RuntimeError(entry["error"])
    if entry["machine_id"]:
        # Keep the same explicit machine selector for validation and mutation.
        current = cli(["--machine", entry["machine_id"], "agent", "get", entry["pane_id"]])["agent"]
    else:
        current = rpc("pane.get", {"pane_id": entry["pane_id"]})["pane"]
    if entry.get("terminal_id") and current.get("terminal_id") != entry["terminal_id"]:
        raise RuntimeError("That pane was replaced since the list loaded; pick it again")


def focus(entry):
    validate(entry)
    if entry["machine_id"]:
        cli(["--machine", entry["machine_id"], "agent", "focus", entry["pane_id"]])
    else:
        rpc("pane.focus", {"pane_id": entry["pane_id"]})
    try:
        record(entry["key"])
    except sqlite3.Error:
        pass  # Ranking history is a nicety; the focus already happened.


def close(entry):
    if entry.get("machine_id"):
        raise RuntimeError("Closing panes on other machines isn't supported")
    validate(entry)
    rpc("pane.close", {"pane_id": entry["pane_id"]})


def read(entry, lines, stop=None):
    """The pane's most recent `lines` lines, with SGR colors; `stop` cancels a remote read."""
    if entry.get("machine_id"):
        return cli(["--machine", entry["machine_id"], "agent", "read", entry["pane_id"], "--source", "recent",
                    "--lines", str(lines), "--format", "ansi"], stop=stop, raw=True)
    return rpc("pane.read", {"pane_id": entry["pane_id"], "source": "recent", "lines": lines,
                             "format": "ansi", "strip_ansi": False})["read"]["text"]


def record_event():
    """Hook for pane.focused (a visit) and pane.agent_status_changed (when a status began)."""
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
    payload = event.get("data", event)
    pane_id = payload.get("pane_id") or payload.get("pane", {}).get("pane_id")
    if pane_id:
        try:
            pane = rpc("pane.get", {"pane_id": pane_id})["pane"]
        except RuntimeError:
            return  # The pane closed before the hook ran; there is nothing to record.
        key = entry_key(["socket", SOCKET], pane)
        if payload.get("agent_status"):
            record_status(key, payload["agent_status"])
        else:
            record(key)


class Inventory:
    """Collect each server independently; animation and typing never cause API traffic."""

    def __init__(self):
        self.stop = threading.Event()
        self.poked = threading.Event()
        self.lock = threading.Lock()
        self.sources = {}
        self.workers = []
        self.version = 0
        self.cache = None
        self.changed = lambda: None
        self.scores = history()
        self.since, self.seen = statuses(), {}
        self.snapshot = rpc("session.snapshot")["snapshot"]

        if ORIGIN in {pane["pane_id"] for pane in self.snapshot.get("panes", [])}:
            self.snapshot["focused_pane_id"] = ORIGIN  # The invoking client's pane, not another client's.

        self.origin = self.snapshot.get("focused_pane_id")
        self.publish("local", stamp(entries(self.snapshot, origin=self.origin), self.since, self.seen))

    def publish(self, source, values):
        with self.lock:
            if self.sources.get(source) == values:
                return
            self.sources[source] = values
            self.version += 1
        self.changed()

    def current(self):
        """(version, entries in rank order); the version changes only when data does."""
        with self.lock:
            if not self.cache or self.cache[0] != self.version:
                ordered = sorted((e for group in self.sources.values() for e in group),
                                 key=lambda e: rank(e, self.scores))
                self.cache = (self.version, ordered)
            return self.cache

    def poke(self):
        """Refresh local panes now, e.g. right after closing one."""
        self.poked.set()

    def refresh(self, machine=None):
        while not self.stop.is_set():
            try:
                snapshot = (cli(["--machine", machine["id"], "api", "snapshot"], stop=self.stop)["snapshot"]
                            if machine else rpc("session.snapshot")["snapshot"])
                values = (entries(snapshot, machine) if machine else
                          stamp(entries(snapshot, origin=self.origin), self.since, self.seen))
            except Exception as error:  # noqa: BLE001 - a dead refresh thread would freeze the list silently.
                values = [error_entry(machine.get("label", machine["id"]) if machine else "Local", str(error),
                                      machine["id"] if machine else None)]
            self.publish(machine["id"] if machine else "local", values)
            if machine:
                self.stop.wait(8)
            else:
                self.poked.wait(1)
                self.poked.clear()

    def discover(self):
        try:
            for machine in cli(["machine", "list", "--json"], stop=self.stop):
                if machine.get("enabled", True) and not self.stop.is_set():
                    label = machine.get("label") or machine["id"]
                    self.publish(machine["id"], [error_entry(label, "Connecting…", machine["id"], True)])
                    worker = threading.Thread(target=self.refresh, args=({**machine, "label": label},), daemon=True)
                    self.workers.append(worker)
                    worker.start()
        except Exception as error:  # noqa: BLE001 - an odd machine list must not take the picker down.
            if not self.stop.is_set():
                self.publish("discovery", [error_entry("Machines", str(error))])

    def __enter__(self):
        for target in (self.refresh, self.discover):
            thread = threading.Thread(target=target, daemon=True)
            self.workers.append(thread)
            thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.poked.set()
        for worker in list(self.workers):
            worker.join(timeout=1)


def picker():
    from picker_ui import run

    with Inventory() as inventory:
        run(inventory)


def fail(message):
    """Report a fatal error; in the popup, wait so it can be read before the popup closes."""
    print(f"Herdscope: {message}", file=sys.stderr)
    if sys.argv[1:2] in ([], ["ui"]) and sys.stdin.isatty():
        input("Press Enter to close.")
    sys.exit(1)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "ui"
    if action == "record-event":
        record_event()
    elif action == "list":
        scores = history()
        ranked = sorted(entries(rpc("session.snapshot")["snapshot"]), key=lambda e: rank(e, scores))
        print(json.dumps(ranked, indent=2))
    elif action == "ui":
        picker()
    else:
        raise RuntimeError(f"Unknown action: {action}")


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        # NOTE: Herdr runs plain `python3`, which on many Macs is Apple's 3.9.
        fail(f"needs Python 3.11 or newer, but python3 here is {sys.version.split()[0]} ({sys.executable}).")
    try:
        main()
    except KeyboardInterrupt:
        pass
    except ERRORS as error:
        fail(clean(error))
    except Exception as error:  # noqa: BLE001 - anything else still gets a readable message, not a vanishing popup.
        fail(f"unexpected {type(error).__name__}: {clean(error)}")
