"""Measure what the picker costs per keystroke, per refresh and per frame, at any number of panes.

Builds a session of N made-up panes, then times the picker's real work as the UI loop does it:
working out the results and drawing a frame into a 45x170 canvas. No Herdr needed.

With --fzf, it also ranks the same panes for the same queries with `fzf --filter`, for a
reference point outside Herdscope, and checks both rank them the same way.

    python3 tools/benchmark.py [--fzf] [N ...]      # default: 100 1000 10000
"""

import os
import random
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from herdscope import app, fuzzy, inventory, render, theme
from herdscope.terminal import Canvas, Style
from herdscope.view import Item, View

WORDS = ("fix", "add", "refactor", "router", "api", "billing", "webhook", "test", "flaky", "docs", "release",
         "notes", "migrate", "typed", "routes", "cuda")  # fmt: skip
TYPED = ["r", "ro", "rou", "rout", "route", "router", "router ", "router a", "router ap", "router api"]


def snapshot(count, rng):
    """A session with `count` panes over count/20 workspaces; two in three run an agent."""
    workspaces = [{"workspace_id": f"w{i}", "label": f"project-{i}", "number": i} for i in range(max(1, count // 20))]
    tabs, panes, agents = {}, [], []
    for i in range(count):
        workspace = workspaces[i % len(workspaces)]["workspace_id"]
        tab = f"{workspace}:t{i % 4}"
        tabs[tab] = {"tab_id": tab, "label": str(i % 4), "number": i % 4}
        panes.append({"pane_id": f"{workspace}:p{i}", "terminal_id": f"t{i}", "workspace_id": workspace,
                      "tab_id": tab, "terminal_title_stripped": " ".join(rng.sample(WORDS, 4)),
                      "cwd": f"/home/me/code/{rng.choice(WORDS)}"})  # fmt: skip
        if i % 3:
            agents.append({"pane_id": f"{workspace}:p{i}", "agent": rng.choice(["claude", "codex", "opencode"]),
                           "agent_status": rng.choice(["idle", "working", "blocked", "done"])})  # fmt: skip
    return {"workspaces": workspaces, "tabs": list(tabs.values()), "panes": panes, "agents": agents}


def measure(count):
    """Milliseconds for each kind of work, at `count` panes."""
    rng = random.Random(1)
    entries = sorted(inventory.entries(snapshot(count, rng)), key=lambda entry: inventory.rank(entry, {}))
    canvas, palette, view = Canvas(45, 170), render.Palette(theme.load({"theme": {"name": "catppuccin"}})), View()
    with app.Previewer(Style(), lambda entry, lines: "output\n" * lines) as previewer:

        def frame(version, shown):
            began = time.perf_counter()
            render.draw(canvas, palette, view, shown, view.results(version, shown), 0, previewer, wall=0)
            return (time.perf_counter() - began) * 1000

        first = frame(1, entries)
        typing = []
        for query in TYPED:
            view.query, view.cursor = query, len(query)
            typing.append(frame(1, entries))
        backspacing = []
        for query in reversed(TYPED[:-1]):
            view.query, view.cursor = query, len(query)
            backspacing.append(frame(1, entries))
        # Refreshes while searching, each with 1% of panes retitled, as busy agents do.
        refreshes = []
        for version in range(2, 7):
            changed = list(entries)
            for i in rng.sample(range(count), max(1, count // 100)):
                changed[i] = {**changed[i], "title": " ".join(rng.sample(WORDS, 4))}
            refreshes.append(frame(version, changed))
        idle = [frame(6, changed) for _ in range(10)]
    return {
        "first frame": first,
        "keystroke": statistics.mean(typing),
        "keystroke, worst": max(typing),
        "backspace": statistics.mean(backspacing),
        "refresh": statistics.mean(refreshes),
        "frame": statistics.median(idle),
    }


def against_fzf(count):
    """Milliseconds to rank every pane for each typed query from scratch, here and in fzf.

    Herdscope starts each query with empty caches, as fzf starts each process with nothing. fzf
    is timed as the process a picker would run, so its start is part of its cost; that start is
    reported separately too.
    """
    items = [Item(entry) for entry in inventory.entries(snapshot(count, random.Random(1)))]
    lines = "\n".join(item.text for item in items)
    env = {**os.environ, "FZF_DEFAULT_OPTS": ""}

    def fzf(query, text):
        began = time.perf_counter()
        found = subprocess.run(["fzf", "--filter", query, "--tiebreak=index"], input=text, env=env,
                               capture_output=True, text=True, check=False).stdout.splitlines()  # fmt: skip
        return (time.perf_counter() - began) * 1000, found

    ours, theirs, same = [], [], True
    for query in TYPED:
        fuzzy.bonuses.cache_clear()
        fuzzy.haystacks.cache_clear()
        began = time.perf_counter()
        ranked = fuzzy.rank(items, fuzzy.Query(query), lambda item: item.text)
        ours.append((time.perf_counter() - began) * 1000)
        elapsed, found = fzf(query, lines)
        theirs.append(elapsed)
        same = same and [item.text for item in ranked] == found
    return {
        "herdscope": statistics.mean(ours),
        "fzf --filter": statistics.mean(theirs),
        "  of which start": statistics.median(fzf("x", "")[0] for _ in range(5)),
        "same ranking": same,
    }


def table(title, results):
    counts = list(results)
    print(f"{title:<18}" + "".join(f"{f'{count:,} panes':>14}" for count in counts))
    for name in results[counts[0]]:
        cells = (results[count][name] for count in counts)
        print(f"{name:<18}" + "".join(f"{'yes' if cell is True else 'NO' if cell is False else f'{cell:.2f}':>14}"
                                      for cell in cells))  # fmt: skip


def main():
    compare = "--fzf" in sys.argv[1:]
    counts = [int(arg) for arg in sys.argv[1:] if arg != "--fzf"] or [100, 1000, 10000]
    table("ms", {count: measure(count) for count in counts})
    if compare:
        if not shutil.which("fzf"):
            sys.exit("--fzf needs fzf on PATH")
        print()
        table("rank, ms/query", {count: against_fzf(count) for count in counts})


if __name__ == "__main__":
    main()
