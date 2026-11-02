"""The picker's state and how input changes it: the query, the tab, the selection, the results.

Nothing here draws or talks to Herdr. `handle` turns an input event into a change of state,
and returns an action ("accept", "run", "close" or "quit") for the app to carry out.
"""

import re
import time
from collections import Counter

from herdscope import fuzzy, herdr, inventory

SCOPES = ("All", "Agents", "Terminals", "Commands")
SCOPE_KINDS = (None, "agent", "terminal", "command")
COMMANDS = SCOPE_KINDS.index("command")
# A row's searchable fields, in the order fzf sees them as one line.
FIELDS = ("label", "detail", "place", "agent", "status", "path", "machine", "category", "target", "aliases")
QUIT_KEYS = ("esc", "ctrl-c", "ctrl-g", "ctrl-q")
ACCEPT_KEYS = ("enter", "ctrl-y")
STEPS = {"up": -1, "ctrl-p": -1, "ctrl-k": -1, "down": 1, "ctrl-n": 1, "ctrl-j": 1}
DOUBLE_CLICK = 0.4  # Seconds between the clicks of a double-click.
RECENT = 32  # Queries whose matches are kept, so backspacing over what was typed costs nothing.


def tally(kinds):
    """How many of `kinds` each tab lists: All, Agents, Terminals, Commands."""
    counts = Counter(kinds)
    return [counts.total() - counts["command"], counts["agent"], counts["terminal"], counts["command"]]


class Item:
    """An entry as the picker shows and searches it, built once per change to the entry."""

    __slots__ = ("entry", "fields", "id", "kind", "spans", "text")

    def __init__(self, entry):
        self.entry, self.id, self.kind = entry, entry["id"], entry["kind"]
        if entry["kind"] == "error":
            values = {"label": entry["machine"], "status": entry["status"], "path": entry["error"]}
        elif entry["kind"] == "command":
            values = {
                "label": entry["title"],
                "category": entry["category"],
                "target": entry["target"],
                "aliases": entry["aliases"],
            }
        else:
            label, detail = inventory.labels(entry)
            values = {
                "label": label,
                "detail": detail,
                "place": f"{entry['workspace']}:{entry['tab']}",
                "agent": entry["agent"],
                "status": entry["status"],
                "path": inventory.display_path(entry) if entry["kind"] == "agent" else "",
                # Only other machines are named: "Local" on every row would be noise to read and match.
                "machine": entry["machine"] if entry.get("machine_id") else "",
            }
        self.fields, self.spans, parts, at = values, {}, [], 0
        # One search line, as fzf sees it; spans map match positions back to fields.
        for name in FIELDS:
            value = values.get(name, "")
            if value:
                self.spans[name] = (at, at + len(value))
                parts.append(value)
                at += len(value) + 1
        self.text = " ".join(parts)

    def highlights(self, positions):
        """Matched positions in the search line as {field: positions within that field}."""
        result = {}
        for position in positions:
            for name, (start, end) in self.spans.items():
                if start <= position < end:
                    result.setdefault(name, set()).add(position - start)
        return result


class View:
    def __init__(self):
        self.query = ""
        self.cursor = 0
        self.scope = 0
        self.selected = None
        self.index = 0
        self.offset = 0
        self.preview = True
        self.help = False
        self.preview_scroll = 0
        self.confirm = None
        self.closing = None
        self.running = None
        self.prompt = None  # The command being given text, while the prompt is its input.
        self.stash = ("", 0)  # The search, put aside while the prompt is an input.
        self.back = 0  # The tab Backspace returns to from Commands.
        self.notice = None
        self.click = (0, None)
        self.hits = {}
        self.page = 10
        self.items = (None, [])
        self.totals = [0] * len(SCOPES)
        self.loading = False
        self.key = None
        self.compiled = fuzzy.Query("")
        self.matched = []
        self.recent = (None, {})  # Matches of recent queries, for one version of the inventory.
        self.marks = {}  # Highlights of rows drawn since the matches last changed.
        self.tally = [0] * len(SCOPES)
        self.rows = []
        self.lines = []
        self.cols = (None, [])

    def results(self, version, entries):
        """The current tab's matches for the query, best first, as Items.

        This runs every frame, so it only works when the inventory, query or tab changed, and
        then only on what changed: thousands of panes must not cost every frame or key press.
        """
        if self.items[0] != version:
            # A refresh rebuilds only the items of panes that changed. The inventory hands back
            # the same entry for a pane that didn't, so most need no comparing at all.
            old = {item.id: item for item in self.items[1]}
            self.items = (
                version,
                [
                    item
                    if (item := old.get(entry["id"])) and (item.entry is entry or item.entry == entry)
                    else Item(entry)
                    for entry in entries
                ],
            )
            self.totals = tally(entry["kind"] for entry in entries)
            self.loading = any(entry.get("status") == "connecting" or entry.get("stale") for entry in entries)
        query = self.search
        key = (version, query, self.scope)
        if key != self.key:
            reset = not self.key or self.key[1:] != key[1:]
            if not self.key or self.key[:2] != key[:2]:
                self.matched = self.match(version, query)
                self.marks = {}
                self.tally = tally(item.kind for item in self.matched)
            # All lists every pane but no commands; the other tabs, one kind each.
            if scope := SCOPE_KINDS[self.scope]:
                self.rows = [item for item in self.matched if item.kind == scope]
            else:
                self.rows = [item for item in self.matched if item.kind != "command"]
            self.lines = self.spaced()
            self.key = key
            if reset:
                # Like fzf: a new query puts the cursor on the best match.
                self.selected, self.index, self.offset = None, 0, 0
        if self.index >= len(self.rows) or self.rows[self.index].id != self.selected:
            # Live refreshes keep the selected destination; if it vanished, keep its position.
            before = self.selected
            found = next((i for i, item in enumerate(self.rows) if item.id == before), None) if before else None
            self.index = min(self.index, max(0, len(self.rows) - 1)) if found is None else found
            self.selected = self.rows[self.index].id if self.rows else None
            if self.selected != before:
                self.preview_scroll = 0  # A different pane's preview starts at its newest output.
        return self.rows

    def match(self, version, query):
        """Every kind's matches, ranked once so each tab can count its own."""
        if self.compiled.text != query:
            self.compiled = fuzzy.Query(query, self.compiled)
        if self.recent[0] != version:
            self.recent = (version, {})
        recent = self.recent[1]
        matched = recent.pop(query, None)
        if matched is None:
            candidates = self.items[1]
            before = self.key[1] if self.key and self.key[0] == version else ""
            if before and query.startswith(before) and not any(char in query for char in "!|\\$'"):
                # Typing on can only narrow plain terms' matches, so only those need checking, as in fzf.
                kept = set(map(id, self.matched))
                candidates = [item for item in candidates if id(item) in kept]
            matched = fuzzy.rank(candidates, self.compiled, lambda item: item.text)
        recent[query] = matched
        if len(recent) > RECENT:
            del recent[next(iter(recent))]
        return matched

    def highlights(self, item):
        """Where the query matched `item`, as {field: positions}: worked out only for rows drawn."""
        marks = self.marks.get(item)
        if marks is None:
            found = self.compiled.match(item.text) if self.compiled else None
            marks = self.marks[item] = item.highlights(found[1]) if found else {}
        return marks

    def spaced(self):
        """Row indexes to draw, with None for a blank line between kinds while browsing everything."""
        if self.search or self.scope not in (0, COMMANDS):
            return range(len(self.rows))
        lines, previous = [], None
        for i, item in enumerate(self.rows):
            group = item.entry.get("category") or item.kind
            if previous and group != previous:
                lines.append(None)
            previous = group
            lines.append(i)
        return lines

    def current(self):
        return self.rows[self.index] if self.rows else None

    @property
    def search(self):
        """The text results are ranked by: the query, or the one put aside while giving input."""
        return self.stash[0] if self.prompt else self.query

    def counts(self):
        """Matches per tab: All, Agents, Terminals, Commands."""
        return self.tally

    def ask(self, entry):
        """Turn the prompt into an input for `entry`, starting from its current value."""
        self.prompt, self.stash = entry["id"], (self.query, self.cursor)
        self.query = entry["ask"]
        self.cursor = len(self.query)

    def unask(self):
        self.query, self.cursor = self.stash
        self.prompt = None

    def find(self, entry_id):
        return next((item for item in self.rows if item.id == entry_id), None)

    def move(self, step):
        if self.rows:
            self.index = max(0, min(len(self.rows) - 1, self.index + step))
            self.selected = self.rows[self.index].id
            self.preview_scroll = 0

    def say(self, text, role="overlay1", seconds=3.5):
        self.notice = (text, role, time.monotonic() + seconds)

    def edit(self, name, text=None):
        """Apply a line-editing key; returns True when handled."""
        query, cursor = self.query, self.cursor
        left = query[:cursor]
        word_start = len(re.sub(r"\S*\s*$", "", left)) if left else 0
        word_end = cursor + len(re.match(r"\s*\S*", query[cursor:]).group())
        if text is not None:
            query, cursor = left + text + query[cursor:], cursor + len(text)
        elif name in ("left", "ctrl-b"):
            cursor = max(0, cursor - 1)
        elif name in ("right", "ctrl-f"):
            cursor = min(len(query), cursor + 1)
        elif name in ("home", "ctrl-a"):
            cursor = 0
        elif name in ("end", "ctrl-e"):
            cursor = len(query)
        elif name in ("alt-b", "ctrl-left", "alt-left"):
            cursor = word_start
        elif name in ("alt-f", "ctrl-right", "alt-right"):
            cursor = word_end
        elif name == "backspace":
            query, cursor = query[: max(0, cursor - 1)] + query[cursor:], max(0, cursor - 1)
        elif name in ("delete", "ctrl-d"):
            query = left + query[cursor + 1 :]
        elif name == "ctrl-u":
            query, cursor = query[cursor:], 0
        elif name in ("ctrl-w", "alt-backspace"):
            query, cursor = query[:word_start] + query[cursor:], word_start
        elif name == "alt-d":
            query = left + query[word_end:]
        else:
            return False
        self.query, self.cursor = query, cursor
        return True


# Input ---------------------------------------------------------------------------------------


def handle(view, event, now):
    """Update the view for one input event; returns "quit", "accept", "close", "run" or None."""
    kind, value = event
    if view.help:
        # Any key or click closes the help, and does nothing else.
        view.help = kind == "mouse" and not value[3]
        return None
    if view.prompt:
        return answer(view, event)
    if view.confirm:
        if kind == "mouse" and not value[3]:
            return None  # The release of the click that asked, say a double-click's.
        # Only y confirms, as the footer says. Act on exactly what was asked about,
        # even if a refresh moved the selection.
        confirmed = event in (("text", "y"), ("text", "Y"))
        chosen, view.confirm = view.confirm if confirmed else None, None
        if not chosen:
            return None
        if chosen.startswith("command:"):
            view.running = chosen
            return "run"
        view.closing = chosen
        return "close"
    if kind == "mouse":
        return mouse(view, value, now)
    if kind in ("text", "paste"):
        text = herdr.clean(value) if kind == "paste" else value
        if text == "?" and not view.query:
            view.help = True  # Like ">" for commands: on an empty search, "?" opens the help.
            return None
        if text.startswith(">") and not view.query and view.scope != COMMANDS:
            # ">" opens Commands and becomes the prompt symbol instead of part of the query.
            view.back, view.scope, text = view.scope, COMMANDS, text[1:]
        view.edit(None, text)
        return None
    if value in QUIT_KEYS:
        return "quit"
    if value in ACCEPT_KEYS:
        if not view.rows:
            return None
        entry = view.current().entry
        if entry["kind"] != "command":
            return "accept"
        if entry["ask"] is not None:
            view.ask(entry)
        elif entry["confirm"]:
            view.confirm = view.selected
        else:
            view.running = view.selected
            return "run"
        return None
    if value in STEPS:
        view.move(STEPS[value])
    elif value in ("pgup", "pgdn"):
        view.move(view.page if value == "pgdn" else -view.page)
    elif value in ("tab", "shift-tab"):
        view.scope = (view.scope + (1 if value == "tab" else -1)) % len(SCOPES)
    elif value == "f1":
        view.help = True
    elif value in ("ctrl-/", "alt-p"):
        view.preview = not view.preview
    elif value in ("shift-up", "shift-down", "shift-pgup", "shift-pgdn"):
        step = 3 if value in ("shift-up", "shift-down") else max(1, view.page // 2)
        view.preview_scroll = max(0, view.preview_scroll + (step if "up" in value else -step))
    elif value == "ctrl-x" and view.rows:
        entry = view.current().entry
        if entry.get("machine_id"):
            view.say("Panes on other machines can't be closed from here", "red")
        elif entry["kind"] in ("agent", "terminal"):
            view.confirm = view.selected
    elif value == "backspace" and view.scope == COMMANDS and not view.query:
        view.scope = view.back if view.back != COMMANDS else 0  # Like deleting the ">".
    else:
        view.edit(value)
    return None


def answer(view, event):
    """Keys while a command asks for text: edit it, Enter runs the command, Esc goes back to the list."""
    kind, value = event
    if kind in ("text", "paste"):
        view.edit(None, herdr.clean(value) if kind == "paste" else value)
    elif value in ACCEPT_KEYS:
        view.running = view.prompt
        return "run"
    elif value in QUIT_KEYS:
        view.unask()
    elif kind == "key":
        view.edit(value)
    return None


def mouse(view, value, now):
    button, x, y, pressed = value
    preview = view.hits.get("preview")
    in_preview = preview and preview[0] <= x < preview[0] + preview[2] and preview[1] <= y < preview[1] + preview[3]
    if button & 64:  # Wheel
        up = not button & 1
        if in_preview:
            view.preview_scroll = max(0, view.preview_scroll + (3 if up else -3))
        else:
            view.move(-1 if up else 1)
        return None
    if not pressed or button & 3 or in_preview:
        return None  # The preview shares the list's lines, so a click there must not pick a row.
    if y == view.hits.get("tabs_y"):
        for start, end, scope in view.hits["tabs"]:
            if start <= x < end:
                view.scope = scope
        return None
    if y in view.hits["rows"]:
        index = view.hits["rows"][y]
        double = view.click[1] == index and now - view.click[0] < DOUBLE_CLICK
        view.click = (now, index)
        view.move(index - view.index)
        return handle(view, ("key", "enter"), now) if double else None  # Exactly what Enter does.
    return None
