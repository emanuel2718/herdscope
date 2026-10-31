"""Herdscope's entry points, as Herdr's manifest calls them.

    python3 -m herdscope [ui]          the picker, in Herdr's popup
    python3 -m herdscope record-visit  the pane.focused hook
    python3 -m herdscope list          the ranked inventory as JSON, for debugging

This file must parse on old Pythons, so that one too old can say so.
"""

import sys

USAGE = "usage: python3 -m herdscope [ui | record-visit | list]"


def fail(message):
    """Report a fatal error; in the popup, wait so it can be read before the popup closes."""
    print(f"Herdscope: {message}", file=sys.stderr)
    if sys.argv[1:2] in ([], ["ui"]) and sys.stdin.isatty():
        input("Press Enter to close.")
    sys.exit(1)


def main(action):
    # Each action imports only what it needs: the hooks run often, and should start fast.
    if action == "ui":
        from herdscope import app

        app.main()
    elif action == "record-visit":
        from herdscope import history

        history.record_visit()
    elif action == "list":
        import json

        from herdscope import herdr, history, inventory

        scores = history.visits()
        ranked = sorted(inventory.entries(herdr.rpc("session.snapshot")["snapshot"]),
                        key=lambda entry: inventory.rank(entry, scores))
        print(json.dumps(ranked, indent=2))
    else:
        print(USAGE, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        # Herdr runs plain `python3`, which on many Macs is Apple's 3.9.
        fail(f"needs Python 3.11 or newer, but python3 here is {sys.version.split()[0]} ({sys.executable}).")
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "ui")
    except KeyboardInterrupt:
        pass
    except Exception as error:
        import sqlite3

        from herdscope import herdr

        if isinstance(error, (*herdr.ERRORS, sqlite3.Error)):
            fail(herdr.clean(error))
        # Anything else still gets a readable message, not a vanishing popup.
        fail(f"unexpected {type(error).__name__}: {herdr.clean(error)}")
