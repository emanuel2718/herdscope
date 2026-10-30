"""Herdr commands for the Commands tab: what each one does, what it acts on, and how it runs.

Commands act on where the popup was opened from: the pane Herdr had focused when the picker
started (a popup is never focused itself), its tab and its workspace.
"""

import os
import shutil
import subprocess

import picker

# Herdr 0.9.1's default bindings, shown for commands the config leaves unset.
DEFAULT_KEYS = {"prefix": "ctrl+b", "new_workspace": "prefix+shift+n", "rename_workspace": "prefix+shift+w",
                "close_workspace": "prefix+shift+d", "new_tab": "prefix+c", "rename_tab": "prefix+shift+t",
                "close_tab": "prefix+shift+x", "split_vertical": "prefix+v", "split_horizontal": "prefix+minus",
                "zoom": "prefix+z", "rename_pane": "prefix+shift+p", "close_pane": "prefix+x",
                "new_worktree": "prefix+shift+g", "reload_config": "prefix+shift+r"}
MODIFIERS = {"ctrl": "⌃", "control": "⌃", "shift": "⇧", "alt": "⌥", "option": "⌥", "super": "⌘", "cmd": "⌘"}
KEY_NAMES = {"comma": ",", "period": ".", "dot": ".", "minus": "-", "plus": "+", "equal": "=", "ampersand": "&",
             "dollar": "$", "semicolon": ";", "slash": "/", "backslash": "\\", "backtick": "`", "space": "␣",
             "tab": "⇥", "enter": "↵", "left": "←", "right": "→", "up": "↑", "down": "↓", "escape": "esc"}
# Agents Herdr can start whose command shares the agent's name. Cursor is left out:
# `cursor` on a PATH is usually the editor's launcher, not its agent.
AGENTS = ("claude", "codex", "opencode", "gemini", "amp", "pi", "copilot", "kimi", "kiro", "droid", "grok", "qwen",
          "cline", "kilo", "letta", "hermes", "muse", "maki", "mastracode", "omp", "qodercli", "devin", "agy")


def chord(binding, prefix=None):
    """A Herdr binding as compact keys: "prefix+shift+n" with prefix "ctrl+s" reads "⌃S ⇧N"."""
    if isinstance(binding, list):
        binding = binding[0] if binding else ""
    if not binding:
        return ""
    tokens, steps = binding.split("+"), []
    if tokens[0] == "prefix" and prefix:
        steps.append(chord(prefix).replace(" ", ""))
        tokens = tokens[1:]
    modifiers = "".join(MODIFIERS.get(token, token) for token in tokens[:-1])
    key = KEY_NAMES.get(tokens[-1], tokens[-1])
    steps.append(modifiers + (key.upper() if modifiers and len(key) == 1 else key))
    return " ".join(steps)


def needed(text, what="a name"):
    if not (text or "").strip():
        raise RuntimeError(f"Type {what} first")
    return text.strip()


def in_git(path):
    while path and path != os.path.dirname(path):
        if os.path.exists(os.path.join(path, ".git")):
            return True
        path = os.path.dirname(path)
    return False


def plural(count, noun):
    return f"{count} {noun}{'s' * (count != 1)}"


def later(*args):
    """Run a herdr command detached, once the popup has closed: it may need the screen to itself."""
    subprocess.Popen(["sh", "-c", 'sleep 0.3; "$@"', "sh", *args], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def command(name, glyph, title, category, target, binding, aliases, run, about, ask=None, confirm=None, **extra):
    """A command as an entry the picker lists, ranks and selects like a pane.

    `ask` is the text to start editing from when the command needs input, `confirm` the
    question to answer first for a destructive one; `run(text)` does the work. With `ask`,
    `blank` says what an empty answer means, or is None when one isn't allowed.
    """
    return {"id": "command:" + name, "key": None, "kind": "command", "machine_id": None, "machine": "",
            "status": "", "glyph": glyph, "title": title + ("…" if ask is not None else ""), "category": category,
            "target": target, "binding": binding, "aliases": aliases, "about": about, "ask": ask,
            "confirm": confirm, "run": run, **extra}


def catalog(snapshot, settings, actions=()):
    """Commands for where the popup was opened from, given the snapshot taken at startup."""
    keys = {**DEFAULT_KEYS, **settings.get("keys", {})}

    def shortcut(name):
        return chord(keys.get(name), keys["prefix"]) if name else ""

    panes = {entry["pane_id"]: entry for entry in picker.entries(snapshot)}
    workspaces = sorted(snapshot.get("workspaces", []), key=lambda w: w.get("number", 0))
    pane = panes.get(snapshot.get("focused_pane_id"))
    cwd = (pane["cwd"] or None) if pane else None
    result = [command("new_workspace", "+", "New workspace", "workspace", "", shortcut("new_workspace"),
                      "create space open", lambda text: picker.rpc("workspace.create", {
                          "cwd": cwd, "focus": True, "label": (text or "").strip() or None}),
                      "Creates a workspace and switches to it. Leave the name empty to name it after its folder.",
                      ask="", blank="named after its folder", folder=cwd)]
    if pane:
        workspace_id, tab_id, pane_id = pane["workspace_id"], pane["tab_id"], pane["pane_id"]
        workspace, place, label = pane["workspace"], f"{pane['workspace']}:{pane['tab']}", picker.labels(pane)[0]
        in_workspace = [e["id"] for e in panes.values() if e["workspace_id"] == workspace_id]
        in_tab = [e["id"] for e in panes.values() if e["tab_id"] == tab_id]
        order = [w["workspace_id"] for w in workspaces]
        index = order.index(workspace_id) if workspace_id in order else -1
        sidebar = [(w.get("number", i + 1), picker.clean(w.get("label", "")), w["workspace_id"] == workspace_id)
                   for i, w in enumerate(workspaces)]

        def move(before):
            return lambda text: picker.rpc("workspace.move_block",
                                           {"workspace_ids": [workspace_id], "before_workspace_id": before})

        result += [
            command("rename_workspace", "✎", "Rename workspace", "workspace", workspace, shortcut("rename_workspace"),
                    "space title", lambda text: picker.rpc("workspace.rename", {
                        "workspace_id": workspace_id, "label": needed(text)}),
                    "Renames the workspace you came from.", ask=workspace, blank=None, was=workspace,
                    sidebar=sidebar),
        ]
        if index > 0:
            result.append(command("move_workspace_up", "↑", "Move workspace up", "workspace", workspace, "",
                                  "space reorder", move(order[index - 1]),
                                  "Moves the workspace you came from one place up in the sidebar.", sidebar=sidebar))
        if 0 <= index < len(order) - 1:
            result.append(command("move_workspace_down", "↓", "Move workspace down", "workspace", workspace, "",
                                  "space reorder", move(order[index + 2] if index + 2 < len(order) else None),
                                  "Moves the workspace you came from one place down in the sidebar.",
                                  sidebar=sidebar))
        result += [
            command("close_workspace", "×", "Close workspace", "workspace", workspace, shortcut("close_workspace"),
                    "space kill delete", lambda text: picker.rpc("workspace.close", {"workspace_id": workspace_id}),
                    f"Closes {workspace} and everything running in it:",
                    confirm=f"Close workspace {workspace}? Its {plural(len(in_workspace), 'pane')} stop.",
                    affects=in_workspace),
            command("new_tab", "+", "New tab", "tab", workspace, shortcut("new_tab"), "create window open",
                    lambda text: picker.rpc("tab.create", {"workspace_id": workspace_id, "cwd": cwd, "focus": True,
                                                           "label": (text or "").strip() or None}),
                    f"Opens a tab in {workspace} and switches to it. Leave the name empty for a numbered tab.",
                    ask="", blank="a numbered tab", folder=cwd),
            command("rename_tab", "✎", "Rename tab", "tab", place, shortcut("rename_tab"), "window title",
                    lambda text: picker.rpc("tab.rename", {"tab_id": tab_id, "label": needed(text)}),
                    f"Renames tab {pane['tab']} in {workspace}.", ask=pane["tab"], blank=None, was=pane["tab"]),
            command("close_tab", "×", "Close tab", "tab", place, shortcut("close_tab"), "window kill delete",
                    lambda text: picker.rpc("tab.close", {"tab_id": tab_id}),
                    f"Closes tab {pane['tab']} in {workspace} and everything running in it:",
                    confirm=f"Close tab {pane['tab']} in {workspace}? Its {plural(len(in_tab), 'pane')} "
                            f"stop{'s' * (len(in_tab) == 1)}.",
                    affects=in_tab),
            command("split_right", "◫", "Split right", "pane", label, shortcut("split_vertical"), "vertical",
                    lambda text: picker.rpc("pane.split", {"target_pane_id": pane_id, "direction": "right",
                                                           "focus": True, "cwd": cwd}),
                    "Splits the pane you came from and starts a shell beside it.", split="right", pane=pane["id"]),
            command("split_down", "⊟", "Split down", "pane", label, shortcut("split_horizontal"), "horizontal",
                    lambda text: picker.rpc("pane.split", {"target_pane_id": pane_id, "direction": "down",
                                                           "focus": True, "cwd": cwd}),
                    "Splits the pane you came from and starts a shell below it.", split="down", pane=pane["id"]),
            command("zoom", "⤢", "Zoom pane", "pane", label, shortcut("zoom"), "fullscreen maximize unzoom",
                    lambda text: picker.rpc("pane.zoom", {"pane_id": pane_id}),
                    "Toggles zoom on the pane you came from, so it fills its tab."),
            command("rename_pane", "✎", "Rename pane", "pane", label, shortcut("rename_pane"), "title",
                    lambda text: picker.rpc("pane.rename", {"pane_id": pane_id,
                                                            "label": (text or "").strip() or None}),
                    "Names the pane you came from. Leave it empty to go back to its own title.",
                    ask=label, blank="its own title", was=label),
            command("close_pane", "×", "Close pane", "pane", label, shortcut("close_pane"), "kill delete",
                    lambda text: picker.rpc("pane.close", {"pane_id": pane_id}),
                    "Closes the pane you came from and what runs in it:",
                    confirm=f"Close {label}? {'This stops the agent.' if pane['agent'] else 'Its shell stops.'}",
                    affects=[pane["id"]]),
        ]
        for agent in AGENTS:
            if shutil.which(agent):
                result.append(command(f"start_{agent}", "▶", f"Start {agent}", "agent", workspace, "",
                                      "launch run new agent", start(agent, workspace_id, cwd),
                                      f"Opens a tab in {workspace} and starts {agent} in it.", folder=cwd))
        if cwd and in_git(cwd):
            result.append(command("new_worktree", "⑂", "New worktree", "worktree", workspace,
                                  shortcut("new_worktree"), "git branch create",
                                  lambda text: picker.rpc("worktree.create", {
                                      "cwd": cwd, "branch": needed(text, "a branch name"), "focus": True}),
                                  "Creates a Git worktree for a new branch and opens it as a workspace.",
                                  ask="", blank=None, folder=cwd))
    result.append(command("reload_config", "↻", "Reload config", "herdr", "", shortcut("reload_config"),
                          "settings refresh", lambda text: picker.rpc("server.reload_config"),
                          "Reloads Herdr's config file, so key, theme and plugin changes apply."))
    for action in actions:
        if action["plugin_id"] != picker.PLUGIN:
            full = f"{action['plugin_id']}.{action['action_id']}"
            result.append(command(f"plugin:{action['plugin_id']}:{action['action_id']}", "◆",
                                  picker.clean(action.get("title") or action["action_id"]), "plugin",
                                  action["plugin_id"].split(".")[-1], "", "action",
                                  # After the popup closes, as from a key binding, so the action can open its own.
                                  lambda text, full=full: later(picker.HERDR, "plugin", "action", "invoke", full),
                                  f"Runs an action from the {action['plugin_id']} plugin."))
    return result


# Start the agent; retry once in case the new tab's shell hadn't reached its prompt yet, and turn a
# failure into a notification. $0 is herdr, then the agent's name, its kind and the pane.
START = ('start() { "$0" agent start "$1" --kind "$2" --pane "$3" >/dev/null 2>&1; }\n'
         'start "$@" || { sleep 1; start "$@"; } || '
         '"$0" notification show Herdscope --body "Couldn\'t start $2 in the new tab"')


def start(agent, workspace_id, cwd):
    """Open a tab and start `agent` in it, named uniquely: Herdr refuses a name already in use."""
    def run(text):
        tab = picker.rpc("tab.create", {"workspace_id": workspace_id, "cwd": cwd, "focus": True, "label": None})
        pane_id = tab["root_pane"]["pane_id"]
        # Herdr waits until the agent is ready, which outlives the popup, so this runs detached.
        subprocess.Popen(["sh", "-c", START, picker.HERDR, f"{agent}-{pane_id.replace(':', '-')}", agent, pane_id],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    return run


def load(snapshot, settings):
    """The catalog, with other plugins' actions if Herdr lists them."""
    try:
        actions = picker.rpc("plugin.action.list").get("actions", [])
    except picker.ERRORS:
        actions = []
    return catalog(snapshot, settings, actions)
