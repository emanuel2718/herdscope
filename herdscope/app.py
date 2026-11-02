"""The popup itself: draw, wait for input, act, until the user picks a pane, runs a command or quits."""

import threading
import time
from contextlib import suppress

from herdscope import commands, fuzzy, herdr, inventory, render, theme
from herdscope.render import Palette
from herdscope.terminal import Style, Terminal, parse_ansi
from herdscope.view import View, handle

PREVIEWS = 32  # Panes whose latest preview is kept, for flicking back to without a wait.
LOCAL_CADENCE = 0.25  # Seconds between reads of a local pane's preview.
REMOTE_CADENCE = 3  # The same for a pane on another machine, a herdr process and an SSH session each.
IDLE = 1  # Seconds the loop sleeps when nothing on screen moves by itself.


class Previewer:
    """Fetch the selected pane's recent output off the UI thread, at a live cadence."""

    def __init__(self, base, read=None, changed=None):
        self.halt = threading.Event()  # Cancels a slow remote read on exit.
        self.base, self.read = base, read or (lambda entry, lines: inventory.read(entry, lines, self.halt))
        self.changed = changed or (lambda: None)  # Called when a preview's content changes.
        self.condition = threading.Condition()
        self.target = None
        self.asked = 0
        self.cache = {}  # The latest read of recently previewed panes, oldest first.
        self.last = None  # (pane, output) as last read, so unchanged output isn't parsed again.
        self.stopped = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.halt.set()
        with self.condition:
            self.stopped = True
            self.condition.notify()
        self.thread.join(timeout=1)

    def idle(self):
        """Stop polling: nothing is being previewed (hidden, a command, or no rows)."""
        with self.condition:
            self.target = None

    def want(self, entry, lines):
        """Called every frame; only a new pane or a deeper scroll interrupts the cadence."""
        with self.condition:
            old = self.target
            if not old or old[0]["id"] != entry["id"] or old[1] < lines:
                if not old or old[0]["id"] != entry["id"]:
                    self.asked = time.monotonic()
                self.target = (entry, lines)
                self.condition.notify()

    def get(self, entry_id):
        """(lines, error, lines asked for) from the latest read of that pane, or None."""
        with self.condition:
            return self.cache.get(entry_id)

    def waiting(self):
        """Seconds since the current pane was first asked for."""
        return time.monotonic() - self.asked

    def run(self):
        while True:
            with self.condition:
                if self.stopped:
                    return
                target = self.target
            if target:
                entry, lines = target
                try:
                    text = self.read(entry, lines)
                    # Unchanged output costs no parsing and no redraw, unless it was asked for
                    # deeper: then it shows the pane's history ends there, so scrolling stops.
                    result = None
                    if self.last != (entry["id"], text):
                        result = (parse_ansi(text, self.base), None, lines)
                    elif (cached := self.get(entry["id"])) and cached[2] < lines:
                        result = (*cached[:2], lines)
                    self.last = (entry["id"], text)
                except Exception as error:  # A dead preview thread would stop every preview.
                    self.last = None
                    result = ([], herdr.clean(error) or "Preview unavailable", lines)
                if result:
                    with self.condition:
                        self.cache.pop(entry["id"], None)
                        self.cache[entry["id"]] = result
                        if len(self.cache) > PREVIEWS:
                            # Scrolling past hundreds of panes mustn't keep every one's output.
                            del self.cache[next(iter(self.cache))]
                    self.changed()
            with self.condition:
                if self.stopped:
                    return
                if self.target is target:
                    self.condition.wait(REMOTE_CADENCE if target and target[0].get("machine_id") else LOCAL_CADENCE)


def main():
    """Open the picker on this Herdr server's panes, then on other machines' as they answer."""
    with inventory.Inventory() as panes:
        run(panes)


def run(panes):
    """The popup: until the user picks a pane, runs a command or quits."""
    with Terminal() as screen:
        settings = theme.config()
        palette = Palette(theme.load(settings, screen.colors()))
        base = Style(palette.tokens["text"], palette.tokens["panel_bg"])
        catalog = commands.load(panes.snapshot, settings)
        # New panes, statuses and preview output redraw at once instead of on the next tick.
        panes.changed = screen.wake
        try:
            with Previewer(base, changed=screen.wake) as previewer:
                loop(panes, screen, palette, View(), previewer, catalog)
        finally:
            panes.changed = lambda: None  # The screen is about to close.


def loop(panes, screen, palette, view, previewer, catalog):
    """Draw, wait for input and act, until the popup should close."""
    warming = None
    switching = None  # A focus on another machine under way: (entry, label, outcome).
    while True:
        if switching and switching[2]:
            entry, label, (error,) = switching
            switching = None
            if error is None:
                remote_focused(screen, palette, label, entry["machine"])
                return
            view.say(herdr.clean(error), "red", 5)
            panes.poke()
        now = time.monotonic()
        version, entries = panes.current()
        entries = entries + catalog
        rows = view.results(version, entries)
        if view.confirm and not view.find(view.confirm):
            view.confirm = None  # What was asked about is gone: nothing left to confirm.
        render.draw(screen, palette, view, entries, rows, now, previewer, view.loading)
        screen.refresh()
        if warming is None:
            # With the first frame up, ready every pane for matching while the user reads it.
            warming = threading.Thread(target=fuzzy.warm, args=([item.text for item in view.items[1]],), daemon=True)
            warming.start()
        try:
            event = screen.event(wait(view, now))
        except EOFError:
            return
        if not event:
            continue
        action = handle(view, event, time.monotonic())  # The time it arrived, for double-clicks.
        if action == "quit":
            return
        if action == "run":
            item = view.find(view.running)
            try:
                if not item:
                    raise RuntimeError("That command is no longer available")
                item.entry["run"](view.query if view.prompt else None)
            except herdr.ERRORS as error:
                view.say(herdr.clean(error), "red", 5)
                panes.poke()
                continue
            return
        if action == "close":
            item = view.find(view.closing)
            try:
                if not item:
                    raise RuntimeError("That pane is already gone")
                inventory.close(item.entry)
                view.say(f"Closed {item.fields['label']}")
            except herdr.ERRORS as error:
                view.say(herdr.clean(error), "red")
            panes.poke()
        if action == "accept":
            entry = view.current().entry
            if entry["kind"] == "error":
                view.say(entry["error"], "red")
                continue
            if entry.get("machine_id"):
                # It takes a herdr process and an SSH round trip or two, so the list stays live
                # meanwhile, and quitting cancels it.
                if not switching:
                    label = view.current().fields["label"]
                    view.say(f"Switching to {label} on {entry['machine']}…", seconds=15)
                    outcome = in_background(lambda entry=entry: inventory.focus(entry, panes.stop), screen.wake)
                    switching = (entry, label, outcome)
                continue
            try:
                inventory.focus(entry)
            except herdr.ERRORS as error:
                view.say(herdr.clean(error), "red", 5)
                panes.poke()
                continue
            return


def in_background(work, done):
    """Run `work` on a thread of its own. The list returned gets its error, or None, then `done` runs."""
    outcome = []

    def run():
        try:
            work()
            outcome.append(None)
        except Exception as error:  # Reported, not raised: the UI thread decides what to show.
            outcome.append(error)
        done()

    threading.Thread(target=run, daemon=True).start()
    return outcome


def wait(view, now):
    """How long the loop may sleep before the screen changes by itself, if no input comes.

    Spinners advance every 0.1 s, so while one shows the loop wakes for its next frame. Otherwise
    only a notice running out, or a status time ticking over a minute, changes what's drawn.
    """
    if view.hits.get("animated"):
        return 0.1 - now % 0.1 + 0.002
    if view.notice and view.notice[2] > now:
        return min(IDLE, view.notice[2] - now + 0.01)
    return IDLE


def remote_focused(screen, palette, label, machine):
    """Herdr has no plugin API to switch the client's displayed machine, so say where to look."""
    render.draw_message(
        screen,
        palette,
        [
            (f"Focused {label}", "text"),
            (f"Select {machine} in Herdr's sidebar to see it.", "subtext0"),
            ("Press any key to close", "overlay0"),
        ],
    )
    screen.refresh()
    with suppress(EOFError):
        while True:
            event = screen.event(1)
            # Not the release of the click that chose the pane, which can come after.
            if event and not (event[0] == "mouse" and not event[1][3]):
                return
