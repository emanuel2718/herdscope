"""Visit history and status times: what Herdscope remembers between openings."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from herdscope import herdr, history, inventory
from tests.support import ROOT, StateTestCase, snapshot


class HistoryTests(StateTestCase):
    def test_frecency_persists_decays_and_deduplicates_hook(self):
        history.record("pane", now=100)
        history.record("pane", now=101)
        with history.database() as db:
            self.assertEqual(db.execute("SELECT weight FROM visits").fetchone()[0], 1)
        history.record("pane", now=100 + history.HALF_LIFE)
        with history.database() as db:
            self.assertEqual(db.execute("SELECT weight FROM visits").fetchone()[0], 1.5)
        self.assertEqual(history.visits()["pane"][1], 100 + history.HALF_LIFE)

    def test_what_herdscope_stores_only_its_user_can_read(self):
        herdr.STATE.chmod(0o755)  # As an earlier version left it.
        history.record("key", 1)
        inventory.save_machines({"m": {"at": 1, "entries": []}})
        self.assertEqual(herdr.STATE.stat().st_mode & 0o777, 0o700)
        fresh = herdr.STATE / "fresh"  # The status hook may be the first to write anything.
        event = json.dumps({"pane_id": "w1:p0", "agent_status": "idle"}, separators=(",", ":"))
        env = {**os.environ, "HERDR_PLUGIN_STATE_DIR": str(fresh), "HERDR_PLUGIN_EVENT_JSON": event}
        subprocess.run(["sh", "scripts/record-status.sh"], check=True, cwd=ROOT, env=env)
        self.assertEqual(fresh.stat().st_mode & 0o777, 0o700)

    def test_old_history_is_forgotten(self):
        history.record("old", now=100)
        history.record("new", now=100 + history.FORGET + 1)
        self.assertEqual(set(history.visits()), {"new"})

    def test_focus_hook_records_the_visit_and_clears_out_closed_panes(self):
        data, folder = snapshot(), history.status_dir()
        folder.mkdir(parents=True)
        for name in ("w1:p0", "w9:p1"):  # A live agent's file and a closed one's
            (folder / name).write_text("idle\n")
        panes = {pane["pane_id"]: pane for pane in data["panes"]}

        def rpc(method, params=None):
            if method == "session.snapshot":
                return {"snapshot": data}
            if params["pane_id"] not in panes:
                raise RuntimeError(f"pane {params['pane_id']} not found")
            return {"pane": panes[params["pane_id"]]}

        for pane_id in ("w1:p0", "w9:p1"):  # The second closed before its hook ran: nothing to record.
            event = {"event": "pane_focused", "data": {"pane_id": pane_id}}
            with (
                patch.dict(os.environ, {"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}),
                patch.object(herdr, "rpc", side_effect=rpc) as calls,
            ):
                history.record_visit()
        self.assertEqual(list(history.visits()), [history.pane_key(history.local_scope(), data["panes"][0])])
        self.assertEqual(os.listdir(folder), ["w1:p0"])
        self.assertNotIn(
            "session.snapshot", [c.args[0] for c in calls.call_args_list], "tidying waits PRUNE_EVERY between runs"
        )

    def test_status_hook_records_when_each_status_began(self):
        items, path = inventory.entries(snapshot()), history.status_dir() / "w1:p0"
        key = items[0]["key"]

        def hook(status, pane="w1:p0", at=None):
            # Herdr's compact JSON. A title can't pose as a field: its quotes are escaped.
            event = json.dumps(
                {
                    "event": "pane_agent_status_changed",
                    "data": {"pane_id": pane, "title": 'say "agent_status":"done"', "agent_status": status},
                },
                separators=(",", ":"),
            )
            subprocess.run(
                ["sh", "scripts/record-status.sh"],
                check=True,
                cwd=ROOT,
                env={
                    **os.environ,
                    "HERDR_PLUGIN_STATE_DIR": str(herdr.STATE),
                    "HERDR_SOCKET_PATH": herdr.SOCKET,
                    "HERDR_PLUGIN_EVENT_JSON": event,
                },
            )
            if at:
                os.utime(path, (at, at))

        hook("working", at=100)
        hook("working")
        self.assertEqual(history.statuses(items), {key: ("working", 100)}, "repeats keep the first time")
        hook("blocked", at=200)
        hook("done", pane="../w1:p0")  # Pane IDs name files, so odd ones are ignored.
        hook("bogus")
        self.assertEqual(history.statuses(items), {key: ("blocked", 200)})
        self.assertEqual(os.listdir(history.status_dir()), ["w1:p0"])
        self.assertEqual(history.visits(), {}, "a status change is not a visit")
        Path(herdr.SOCKET).touch()  # Herdr restarted, so pane IDs may name other panes now.
        self.assertEqual(history.statuses(items), {})
        hook("blocked")  # The same status as the old file's, but in the restarted server.
        status, since = history.statuses(items)[key]
        self.assertEqual(status, "blocked")
        self.assertGreaterEqual(since, history.started())

    def test_time_in_state_comes_from_the_hook_or_what_the_picker_saw(self):
        items = inventory.entries(snapshot())
        blocked, working, idle = items[:3]
        recorded = {blocked["key"]: ("blocked", 50), working["key"]: ("idle", 10)}
        seen = {}
        history.stamp(items, recorded, seen, now=100)
        self.assertEqual([blocked["since"], working["since"], idle["since"]], [50, None, None])
        self.assertNotIn("since", items[3], "terminals have no status")
        idle["status"] = "working"
        history.stamp(items, recorded, seen, now=130)
        self.assertEqual(idle["since"], 130, "a change seen while open counts from when it was seen")
