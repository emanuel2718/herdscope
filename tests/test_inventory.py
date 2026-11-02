"""The inventory: entries, ranking, machines, and acting on panes."""

import copy
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from herdscope import herdr, history, inventory
from tests.support import StateTestCase, snapshot


class InventoryTests(StateTestCase):
    def test_attention_order_then_frecency_and_current_pane_sinks(self):
        items = inventory.entries(snapshot())
        frequent = copy.deepcopy(items[1])
        frequent.update(id="another", key="frequent")
        scores = {items[2]["key"]: (10000, 0), frequent["key"]: (10, 0)}
        ordered = sorted(
            [items[4], items[3], items[2], items[1], frequent, items[0]], key=lambda e: inventory.rank(e, scores)
        )
        self.assertEqual(
            [e["id"] for e in ordered],
            [items[0]["id"], "another", items[1]["id"], items[2]["id"], items[3]["id"], items[4]["id"]],
        )
        self.assertTrue(items[4]["current"])

    def test_machine_session_identity_and_pane_moves(self):
        data = snapshot()
        a = inventory.entries(data, {"id": "machine-a", "label": "Box", "session": "agents"})[0]
        b = inventory.entries(data, {"id": "machine-b", "label": "Box", "session": "agents"})[0]
        other = inventory.entries(data, {"id": "machine-a", "label": "Box", "session": "other"})[0]
        self.assertEqual(len({a["key"], b["key"], other["key"]}), 3)
        data["panes"][0]["pane_id"] = "w2:p4"
        moved = inventory.entries(data, {"id": "machine-a", "label": "Renamed", "session": "agents"})[0]
        self.assertEqual(a["key"], moved["key"])
        self.assertEqual(len(inventory.entries(data, {"id": "m", "label": "Box"})), 3, "remote terminals are omitted")

    def test_refresh_survives_unexpected_errors(self):
        replies = iter([{"snapshot": snapshot()}])

        def rpc(method, params=None):
            reply = next(replies, None)
            if reply is None:
                raise TypeError("odd reply")
            return reply

        with patch.object(herdr, "rpc", rpc):
            panes = inventory.Inventory()
            panes.poke()
            worker = threading.Thread(target=panes.refresh, daemon=True)
            worker.start()
            deadline = time.monotonic() + 5  # However slow the machine, wait for the failing refresh.
            while time.monotonic() < deadline and panes.current()[1][0]["kind"] != "error":
                time.sleep(0.01)
            panes.stop.set()
            panes.poked.set()
            worker.join(1)
        self.assertEqual(panes.current()[1][0]["kind"], "error", "an error row, not a dead thread")
        self.assertIn("odd reply", panes.current()[1][0]["error"])

    def test_a_corrupt_history_still_opens_the_picker(self):
        (herdr.STATE / "history.sqlite3").write_bytes(b"not a database" * 100)
        with patch.object(herdr, "rpc", lambda method, params=None: {"snapshot": snapshot()}):
            panes = inventory.Inventory()
        self.assertEqual(panes.scores, {})
        self.assertEqual(len(panes.current()[1]), len(inventory.entries(snapshot())))

    def test_many_machines_share_a_few_connections(self):
        machines = [{"id": f"m{i}", "label": f"Box {i}"} for i in range(40)]
        running, peak, lock = 0, 0, threading.Lock()

        def cli(args, stop=None, **kwargs):
            nonlocal running, peak
            if args[0] == "machine":
                return machines
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.01)
            with lock:
                running -= 1
            return {"snapshot": snapshot()}

        with patch.object(herdr, "rpc", return_value={"snapshot": snapshot()}), patch.object(herdr, "cli", cli):
            with inventory.Inventory() as panes:
                deadline, expected = time.monotonic() + 5, {None} | {m["id"] for m in machines}
                while time.monotonic() < deadline and expected != (
                    loaded := {e["machine_id"] for e in panes.current()[1] if e["kind"] == "agent"}
                ):
                    time.sleep(0.02)
        self.assertEqual(loaded, expected)
        self.assertLessEqual(peak, inventory.MACHINE_WORKERS)
        self.assertEqual(len(panes.workers), 2 + inventory.MACHINE_WORKERS, "not a thread per machine")

    def test_machines_show_last_time_s_panes_until_they_answer(self):
        machines, answer = [{"id": "m", "label": "Box"}], threading.Event()

        def cli(args, stop=None, **kwargs):
            if args[0] == "machine":
                return machines
            answer.wait(5)
            data = snapshot()
            data["panes"][0]["agent_status"] = status
            return {"snapshot": data}

        def remote(panes):
            return [e for e in panes.current()[1] if e["machine_id"] == "m"]

        def wait_for(panes, test):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not test(remote(panes)):
                time.sleep(0.01)
            return remote(panes)

        status = "idle"
        with patch.object(herdr, "rpc", return_value={"snapshot": snapshot()}), patch.object(herdr, "cli", cli):
            answer.set()
            with inventory.Inventory() as panes:
                wait_for(panes, lambda rows: rows and rows[0]["kind"] == "agent")
                status = "blocked"  # Seen to change while open, so its time is known.
                panes.machines["m"]["at"] = 0  # Make the next refresh due now.
                with panes.due:
                    panes.schedule = [(0, 0, 0.01, {**machines[0], "label": "Box"})]
                    panes.due.notify()
                first = wait_for(panes, lambda rows: any(e["status"] == "blocked" for e in rows))
            answer.clear()  # Next time, the machine is slow to answer.
            with inventory.Inventory() as panes:
                cached = wait_for(panes, lambda rows: rows and rows[0]["kind"] == "agent")
                self.assertTrue(all(e["stale"] for e in cached), "last time's panes, until it answers")
                answer.set()
                fresh = wait_for(panes, lambda rows: rows and not rows[0].get("stale"))
        blocked = next(e for e in first if e["status"] == "blocked")
        self.assertIsNotNone(blocked["since"])
        self.assertEqual(
            next(e for e in fresh if e["key"] == blocked["key"])["since"],
            blocked["since"],
            "a status that held keeps its time across openings",
        )

    def test_idle_machines_are_asked_less_often(self):
        calls, machines = {"busy": 0, "idle": 0}, [{"id": "busy", "label": "Busy"}, {"id": "idle", "label": "Idle"}]

        def cli(args, stop=None, **kwargs):
            if args[0] == "machine":
                return machines
            calls[args[1]] += 1
            data = snapshot()
            if args[1] == "busy":
                data["panes"][0]["terminal_title_stripped"] = f"Step {calls['busy']}"
            return {"snapshot": data}

        # Wait for enough rounds for the idle machine to reach the longest wait, then compare the
        # waits the schedule holds: counting calls in a fixed time would depend on the runner's speed.
        with (
            patch.object(herdr, "rpc", return_value={"snapshot": snapshot()}),
            patch.object(herdr, "cli", cli),
            patch.object(inventory, "MACHINE_INTERVAL", 0.02),
            patch.object(inventory, "MACHINE_RATE", 1000),
        ):
            with inventory.Inventory() as panes:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and min(calls.values()) < 4:
                    time.sleep(0.01)
            waits = {machine["id"]: wait for _, _, wait, machine in panes.schedule}
        self.assertGreaterEqual(min(calls.values()), 4)
        self.assertEqual(waits["busy"], 0.02, "a machine whose panes changed is asked again soon")
        self.assertEqual(waits["idle"], 0.02 * inventory.MACHINE_BACKOFF, "one that didn't, less and less often")

    def test_missing_workspace_and_tab_numbers_still_sort(self):
        data = snapshot()
        data["workspaces"][0]["number"] = None
        data["tabs"][0]["number"] = None
        data["workspaces"].append({"workspace_id": "w2", "label": "Beta"})
        data["panes"].append({**data["panes"][3], "pane_id": "w2:p1", "terminal_id": "t-9", "workspace_id": "w2"})
        with patch.object(herdr, "rpc", return_value={"snapshot": data}):
            panes = inventory.Inventory()
        self.assertEqual(len(panes.current()[1]), 6)

    def test_a_machine_named_like_the_local_server_is_kept_apart(self):
        machines = [{"id": "local", "label": "Local"}, {"id": "discovery", "label": "Machines"}]

        def cli(args, stop=None, **kwargs):
            return machines if args[0] == "machine" else {"snapshot": snapshot()}

        with patch.object(herdr, "rpc", return_value={"snapshot": snapshot()}), patch.object(herdr, "cli", cli):
            with inventory.Inventory() as panes:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and len(panes.current()[1]) < 5 + 3 + 3:
                    time.sleep(0.02)
        found = panes.current()[1]
        self.assertEqual(sum(e["machine_id"] is None for e in found), 5, "every local pane")
        self.assertEqual({e["machine_id"] for e in found if e["machine_id"]}, {"local", "discovery"})

    def test_the_invoking_pane_is_where_the_popup_came_from(self):
        with (
            patch.object(herdr, "rpc", return_value={"snapshot": snapshot()}),
            patch.object(inventory, "ORIGIN", "w1:p7"),
        ):
            panes = inventory.Inventory()
        self.assertEqual(panes.snapshot["focused_pane_id"], "w1:p7", "not the other client's focus (w1:p8)")
        self.assertEqual([e["pane_id"] for e in panes.current()[1] if e["current"]], ["w1:p7"])

    def test_remote_focus_never_uses_local_ids(self):
        entry = inventory.entries(snapshot(), {"id": "remote-id", "label": "Remote", "session": "agents"})[0]
        with (
            patch.object(herdr, "cli", side_effect=[{"agent": {"terminal_id": "term-0"}}, {}]) as cli,
            patch.object(herdr, "rpc") as rpc,
        ):
            inventory.focus(entry)
        rpc.assert_not_called()
        self.assertEqual(cli.call_args_list[0].args[0], ["--machine", "remote-id", "agent", "get", "w1:p0"])
        self.assertEqual(cli.call_args_list[1].args[0], ["--machine", "remote-id", "agent", "focus", "w1:p0"])

    def test_stale_destination_does_not_focus_close_or_record(self):
        entry = inventory.entries(snapshot())[0]
        for action in (inventory.focus, inventory.close):
            with patch.object(herdr, "rpc", return_value={"pane": {"terminal_id": "replacement"}}) as rpc:
                with self.assertRaisesRegex(RuntimeError, "replaced"):
                    action(entry)
            self.assertEqual(rpc.call_count, 1)
        self.assertEqual(history.visits(), {})

    def test_close_validates_then_closes_local_panes_only(self):
        entry = inventory.entries(snapshot())[3]
        with patch.object(herdr, "rpc", side_effect=[{"pane": {"terminal_id": "term-7"}}, {}]) as rpc:
            inventory.close(entry)
        self.assertEqual(rpc.call_args_list[1].args, ("pane.close", {"pane_id": "w1:p7"}))
        remote = inventory.entries(snapshot(), {"id": "r", "label": "Remote"})[0]
        with self.assertRaisesRegex(RuntimeError, "other machines"):
            inventory.close(remote)


class HerdrTests(StateTestCase):
    def test_cli_errors_show_herdr_s_message(self):
        fake = Path(self.temp.name) / "herdr"
        error = json.dumps({"error": {"code": "machine_unreachable", "message": "box is offline"}})
        fake.write_text(f"#!/bin/sh\necho '{error}' >&2\nexit 1\n")
        fake.chmod(0o755)
        with patch.object(herdr, "HERDR", str(fake)), self.assertRaisesRegex(RuntimeError, "^box is offline$"):
            herdr.cli(["api", "snapshot"])
