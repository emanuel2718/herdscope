"""Every pane Herdscope lists, from this server and other machines, kept fresh in the background.

Panes become entries: flat dicts with a stable `key` (for history), a `kind` ("agent",
"terminal" or "error") and what the picker shows and searches. Entries are ranked once,
when they change, so the UI only ever re-sorts ready-made keys.
"""

import heapq
import json
import operator
import os
import random
import re
import sqlite3
import threading
import time
from contextlib import suppress
from pathlib import Path

from herdscope import herdr, history

# The pane the popup was opened from; scripts/open.sh passes it on.
ORIGIN = os.environ.get("HERDSCOPE_PANE")
# Forwarded commands at once. Each is a herdr process and an SSH session, so hundreds of
# machines must not mean hundreds of them, or threads, or open files.
MACHINE_WORKERS = 16
MACHINE_INTERVAL = 8
# Forwarded commands a second at most, once every machine has loaded. Each costs about 10 ms of
# CPU here, so past MACHINE_RATE * MACHINE_INTERVAL machines, each is refreshed less often.
MACHINE_RATE = 10
# A machine whose panes didn't change waits twice as long next time, up to this many intervals,
# as does one that can't be reached, which holds a worker until it times out.
MACHINE_BACKOFF = 4
MACHINE_CACHE_AGE = 86400  # Last time's panes show while a machine loads, if no older than this.
STATUS_ORDER = {"blocked": 0, "working": 1, "done": 2, "idle": 3, "unknown": 4}
SECTION = {"agent": 0, "terminal": 1, "error": 2}


# Entries -----------------------------------------------------------------------------------


def display_path(entry):
    """The entry's folder, with the home folder as ~ for local panes."""
    path = entry.get("cwd", "")
    home = str(Path.home())
    if not entry.get("machine_id") and (path == home or path.startswith(home + "/")):
        path = "~" + path[len(home) :]
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


def entries(snapshot, machine=None, origin=None):
    """The snapshot's panes as entries; a remote `machine` lists only its agents."""
    machine_id = machine["id"] if machine else None
    scope = json.dumps(
        ["machine", machine_id, machine.get("session", "default")] if machine else history.local_scope(),
        separators=(",", ":"),
    )
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
        key = history.pane_key(scope, pane)
        result.append(
            {
                "id": key,
                "key": key,
                "kind": "agent" if agent else "terminal",
                "machine_id": machine_id,
                "machine": herdr.clean(machine["label"]) if machine else "Local",
                "pane_id": pane["pane_id"],
                "terminal_id": pane.get("terminal_id"),
                "workspace_id": pane["workspace_id"],
                "tab_id": pane["tab_id"],
                "agent": agent or "",
                "status": (pane.get("agent_status") or "unknown") if agent else "",
                "workspace": herdr.clean(workspace.get("label", pane["workspace_id"])),
                "tab": herdr.clean(tab.get("label", pane["tab_id"].split(":")[-1])),
                "title": herdr.clean(pane.get("title") or pane.get("terminal_title_stripped") or pane.get("label")),
                "cwd": pane.get("foreground_cwd") or pane.get("cwd") or "",
                "current": pane["pane_id"] == focused,
                "order": [workspace.get("number", 0), tab.get("number", 0), position],
            }
        )
    return result


def error_entry(label, message, machine_id=None, pending=False):
    """A row standing in for a server that failed, or a machine still connecting."""
    return {
        "id": json.dumps(["error", machine_id or label]),
        "kind": "error",
        "key": None,
        "machine": herdr.clean(label),
        "machine_id": machine_id,
        "status": "connecting" if pending else "offline",
        "error": herdr.clean(message),
    }


def rank(entry, scores):
    """The sort key with an empty query: attention order (blocked, working, done, idle), then
    frecency, then sidebar order. The pane you are already looking at sinks to the end."""
    visits = scores.get(entry.get("key"), (0, 0))
    return (
        SECTION[entry["kind"]],
        entry.get("current", False),
        STATUS_ORDER.get(entry.get("status"), 4),
        -visits[0],
        entry.get("machine_id") or "",
        entry.get("order", []),
        entry["id"],
    )


# Acting on entries -------------------------------------------------------------------------


def validate(entry):
    """Raise unless the entry's pane still runs the terminal it listed."""
    if entry.get("error"):
        raise RuntimeError(entry["error"])
    if entry["machine_id"]:
        # Keep the same explicit machine selector for validation and mutation.
        current = herdr.cli(["--machine", entry["machine_id"], "agent", "get", entry["pane_id"]])["agent"]
    else:
        current = herdr.rpc("pane.get", {"pane_id": entry["pane_id"]})["pane"]
    if entry.get("terminal_id") and current.get("terminal_id") != entry["terminal_id"]:
        raise RuntimeError("That pane was replaced since the list loaded; pick it again")


def focus(entry):
    validate(entry)
    if entry["machine_id"]:
        herdr.cli(["--machine", entry["machine_id"], "agent", "focus", entry["pane_id"]])
    else:
        herdr.rpc("pane.focus", {"pane_id": entry["pane_id"]})
    with suppress(sqlite3.Error):  # Ranking history is a nicety; the focus already happened.
        history.record(entry["key"])


def close(entry):
    if entry.get("machine_id"):
        raise RuntimeError("Closing panes on other machines isn't supported")
    validate(entry)
    herdr.rpc("pane.close", {"pane_id": entry["pane_id"]})


def read(entry, lines, stop=None):
    """The pane's most recent `lines` lines, with SGR colors; `stop` cancels a remote read."""
    if entry.get("machine_id"):
        return herdr.cli(
            [
                "--machine",
                entry["machine_id"],
                "agent",
                "read",
                entry["pane_id"],
                "--source",
                "recent",
                "--lines",
                str(lines),
                "--format",
                "ansi",
            ],
            stop=stop,
            raw=True,
        )
    return herdr.rpc(
        "pane.read",
        {"pane_id": entry["pane_id"], "source": "recent", "lines": lines, "format": "ansi", "strip_ansi": False},
    )["read"]["text"]


# Remote machines, as last seen ----------------------------------------------------------------


def load_machines():
    """{machine: {"at": when, "entries": panes}} as the picker last saw them."""
    try:
        return json.loads((herdr.STATE / "machines.json").read_text())
    except (OSError, ValueError):
        return {}


def save_machines(machines):
    herdr.STATE.mkdir(parents=True, exist_ok=True)
    temporary = herdr.STATE / f"machines.json.{os.getpid()}"
    temporary.write_text(json.dumps(machines, separators=(",", ":")))
    os.replace(temporary, herdr.STATE / "machines.json")  # Another picker may be reading it.


# The live inventory --------------------------------------------------------------------------


class Inventory:
    """Collect each server independently; animation and typing never cause API traffic."""

    def __init__(self):
        self.stop = threading.Event()
        self.poked = threading.Event()
        self.lock = threading.Lock()
        self.due = threading.Condition()  # Guards the machines' schedule.
        self.schedule = []
        self.machines = {}  # Each machine's latest panes, kept for the next time the picker opens.
        self.sources = {}
        self.workers = []
        self.version = 0
        self.cache = None
        self.changed = lambda: None
        self.scores = history.visits()
        self.snapshot = herdr.rpc("session.snapshot")["snapshot"]

        if ORIGIN in {pane["pane_id"] for pane in self.snapshot.get("panes", [])}:
            self.snapshot["focused_pane_id"] = ORIGIN  # The invoking client's pane, not another client's.

        self.origin = self.snapshot.get("focused_pane_id")
        self.since, self.seen = {}, {}  # Status times follow the first frame; see refresh.
        self.publish("local", history.stamp(entries(self.snapshot, origin=self.origin), self.since, self.seen))

    def publish(self, source, values):
        # Each entry's rank is worked out once, here, so sorting thousands of them after every
        # change compares ready-made keys.
        ranked = [(rank(entry, self.scores), entry) for entry in values]
        with self.lock:
            if self.sources.get(source) == ranked:
                return
            self.sources[source] = ranked
            self.version += 1
        self.changed()

    def current(self):
        """(version, entries in rank order); the version changes only when data does."""
        with self.lock:
            if not self.cache or self.cache[0] != self.version:
                ranked = sorted((pair for group in self.sources.values() for pair in group), key=operator.itemgetter(0))
                self.cache = (self.version, [entry for _, entry in ranked])
            return self.cache

    def poke(self):
        """Refresh local panes now, e.g. right after closing one."""
        self.poked.set()

    def refresh(self):
        """Local panes, every second or right after a poke.

        The first list came with the snapshot. Its status times come next: they are a file
        per agent, which takes a while with thousands, so they follow the first frame rather
        than hold it up. With thousands of panes a refresh takes a while too, so the pause
        stretches to keep refreshing under a twentieth of a core.
        """
        with suppress(Exception):  # Durations are a nicety; the list stands without them.
            self.since.update(history.statuses(entries(self.snapshot)))
            self.publish("local", history.stamp(entries(self.snapshot, origin=self.origin), self.since, self.seen))
        pause = 1
        while True:
            self.poked.wait(pause)
            self.poked.clear()
            if self.stop.is_set():
                return
            began = time.monotonic()
            try:
                snapshot = herdr.rpc("session.snapshot")["snapshot"]
                values = history.stamp(entries(snapshot, origin=self.origin), self.since, self.seen)
            except Exception as error:  # A dead refresh thread would freeze the list silently.
                values = [error_entry("Local", str(error))]
            self.publish("local", values)
            pause = max(1, 20 * (time.monotonic() - began))

    def discover(self):
        try:
            machines = [
                {**machine, "label": machine.get("label") or machine["id"]}
                for machine in herdr.cli(["machine", "list", "--json"], stop=self.stop)
                if machine.get("enabled", True)
            ]
        except Exception as error:  # An odd machine list must not take the picker down.
            if not self.stop.is_set():
                self.publish("discovery", [error_entry("Machines", str(error))])
            return
        # Until a machine answers, show its panes as the picker last saw them, dimmed, so hundreds
        # of machines appear at once. Their status times carry over while the status holds.
        cached, now = load_machines(), time.time()
        for machine in machines:
            last = cached.get(machine["id"])
            if last and now - last["at"] < MACHINE_CACHE_AGE:
                self.machines[machine["id"]] = last
                for entry in last["entries"]:
                    if entry.get("since"):
                        self.since[entry["key"]] = (entry["status"], entry["since"])
                self.publish(machine["id"], [{**entry, "stale": True} for entry in last["entries"]])
            else:
                self.publish(machine["id"], [error_entry(machine["label"], "Connecting…", machine["id"], True)])
        interval = max(MACHINE_INTERVAL, len(machines) / MACHINE_RATE)
        with self.due:
            self.schedule = [(0, order, interval, machine) for order, machine in enumerate(machines)]
        for _ in range(min(MACHINE_WORKERS, len(machines))):
            self.start(self.poll, interval)

    def poll(self, interval):
        """Refresh machines as they fall due, earliest first, backing off those that are idle."""
        while True:
            with self.due:
                while not self.stop.is_set() and (not self.schedule or self.schedule[0][0] > time.monotonic()):
                    self.due.wait(self.schedule[0][0] - time.monotonic() if self.schedule else None)
                if self.stop.is_set():
                    return
                _, order, wait, machine = heapq.heappop(self.schedule)
            try:
                snapshot = herdr.cli(["--machine", machine["id"], "api", "snapshot"], stop=self.stop)["snapshot"]
                values = history.stamp(entries(snapshot, machine), self.since, self.seen)
                last = self.machines.get(machine["id"], {}).get("entries")
                wait = min(wait * 2, interval * MACHINE_BACKOFF) if values == last else interval
                self.machines[machine["id"]] = {"at": time.time(), "entries": values}
            except Exception as error:  # A dead worker would leave its machines stale silently.
                values, wait = [error_entry(machine["label"], str(error), machine["id"])], interval * MACHINE_BACKOFF
            self.publish(machine["id"], values)
            with self.due:
                # A little jitter, so machines first asked together drift apart instead of
                # falling due together every round.
                when = time.monotonic() + wait * random.uniform(0.75, 1.25)
                heapq.heappush(self.schedule, (when, order, wait, machine))
                self.due.notify()

    def start(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        self.workers.append(thread)
        thread.start()

    def __enter__(self):
        self.start(self.refresh)
        self.start(self.discover)
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.poked.set()
        with self.due:
            self.due.notify_all()
        for worker in list(self.workers):
            worker.join(timeout=1)
        if self.machines:
            # On failure, machines show "Connecting…" next time until they answer, as on the first.
            with suppress(OSError):
                save_machines(self.machines)
