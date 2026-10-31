"""What Herdscope remembers between openings: pane visits, for ranking, and when statuses began.

Visits live in SQLite and decay with a half-life, so panes you use often and lately rank
higher. Status times come from scripts/record-status.sh, which keeps a file per agent.
"""

import json
import math
import os
import sqlite3
import time
from contextlib import contextmanager, suppress
from json.encoder import encode_basestring_ascii
from pathlib import Path

from herdscope import herdr

HALF_LIFE = 7 * 86400
FORGET = 90 * 86400  # Visits older than this are dropped.
REPEAT = 3  # Seconds within which a second visit to a pane is the same one, seen twice.
PRUNE_EVERY = 600  # Seconds between clear-outs of the status files, which need every pane.


def pane_key(scope, pane):
    """JSON for [scope, terminal]: terminal IDs survive pane moves, and the machine or session
    keeps two servers' IDs apart. `scope` may come already as JSON, done once per snapshot."""
    scope = scope if isinstance(scope, str) else json.dumps(scope, separators=(",", ":"))
    return f"[{scope},{encode_basestring_ascii(pane.get('terminal_id') or pane['pane_id'])}]"


def local_scope():
    return ["socket", herdr.SOCKET]


# Visits ------------------------------------------------------------------------------------

@contextmanager
def database():
    herdr.STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(herdr.STATE / "history.sqlite3", timeout=5)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS visits (key TEXT PRIMARY KEY, weight REAL, last REAL)")
            yield db
    finally:
        db.close()


def decay(weight, last, now):
    return weight * math.exp2(-max(0, now - last) / HALF_LIFE)


def record(key, now=None):
    """Count a visit to the pane with `key`."""
    now = time.time() if now is None else now
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT weight, last FROM visits WHERE key = ?", (key,)).fetchone()
        # The picker and the pane.focused hook can observe the same visit.
        if old and now - old[1] < REPEAT:
            return
        weight = decay(*old, now) + 1 if old else 1
        db.execute("INSERT OR REPLACE INTO visits VALUES (?, ?, ?)", (key, weight, now))
        db.execute("DELETE FROM visits WHERE last < ?", (now - FORGET,))


def visits():
    """Visit history as {key: (decayed score, last visit time)}."""
    with database() as db:
        now = time.time()
        return {key: (decay(weight, last, now), last) for key, weight, last in db.execute("SELECT * FROM visits")}


# Status times ------------------------------------------------------------------------------

def status_dir():
    """Where record-status.sh keeps a file per local pane: its agent's status, written when that began.

    Plugin state is shared by every Herdr session, so the folder is named after this one's socket.
    """
    return herdr.STATE / "status" / herdr.SOCKET.lstrip("/")


def started():
    """When this Herdr server started, which is when it created its socket; 0 if unknown."""
    try:
        return os.stat(herdr.SOCKET).st_mtime
    except OSError:
        return 0


def statuses(entries):
    """{key: (status, since)} for the local agents in `entries`, as record-status.sh recorded them.

    A file from before Herdr restarted is ignored: its pane ID may name another pane now.
    """
    folder, since_start, result = status_dir(), started(), {}
    for entry in entries:
        if entry["kind"] == "agent":
            try:  # Plain system calls: this runs for every agent.
                fd = os.open(folder / entry["pane_id"], os.O_RDONLY)
                try:
                    status, since = os.read(fd, 64).decode().strip(), os.fstat(fd).st_mtime
                finally:
                    os.close(fd)
            except (OSError, UnicodeError):
                continue
            if since >= since_start:
                result[entry["key"]] = (status, since)
    return result


def stamp(entries, recorded, seen, now=None):
    """Set each agent's "since": when it entered its current status, or None if unknown.

    `recorded` comes from the hook; a change the picker sees itself before the hook lands is
    stamped with the time it was seen. `seen` tracks the last status observed per key.
    """
    now = time.time() if now is None else now
    for entry in entries:
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
    return entries


def prune(snapshot, before):
    """Delete status files of panes that are gone or predate the server, written before `before`.

    Agents come and go all day; without this the folder would gain a file for every one.
    """
    live, since_start = {pane["pane_id"] for pane in snapshot.get("panes", [])}, started()
    try:
        files = list(os.scandir(status_dir()))
    except OSError:
        return
    for file in files:
        with suppress(OSError):  # Already gone, e.g. pruned by a hook running alongside.
            changed = file.stat().st_mtime
            if (file.name not in live or changed < since_start) and changed < before:
                os.unlink(file.path)


# The pane.focused hook ---------------------------------------------------------------------

def record_visit():
    """Count the visit Herdr reported, and now and then tidy record-status.sh's files.

    Focus changes come at a person's pace, unlike status changes, so this hook can afford
    Python. Tidying needs a snapshot of every pane, so it waits PRUNE_EVERY between runs.
    """
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    payload = event.get("data", event)
    pane_id = payload.get("pane_id") or payload.get("pane", {}).get("pane_id")
    now = time.time()
    if pane_id:
        # The pane may have closed before the hook ran; then there is nothing to record.
        with suppress(RuntimeError):
            record(pane_key(local_scope(), herdr.rpc("pane.get", {"pane_id": pane_id})["pane"]), now)
    marker = Path(f"{status_dir()}.pruned")
    try:
        if now - marker.stat().st_mtime < PRUNE_EVERY:
            return
    except OSError:
        marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    prune(herdr.rpc("session.snapshot")["snapshot"], now)
