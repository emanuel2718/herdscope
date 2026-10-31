# Herdscope

A fuzzy finder for everything in [Herdr](https://herdr.dev): every agent, terminal and
command in one list, with a live preview.

![Herdscope: agents and terminals on the left, ranked by attention, with the selected agent's live output on the right](assets/screenshot.png)

- **Everything in one list.** Agents show their task, status and how long they've held it
  (`blocked 2m`). Terminals show their command and folder. Panes on your other machines
  appear alongside local ones.
- **Live previews.** The selected pane's output, colors included, updated as it changes.
- **fzf's matching.** A port of fzf's algorithm and search syntax, tested against fzf
  itself. fzf doesn't need to be installed.
- **Herdr commands.** Type `>` to rename, split, zoom or close, start an agent, make a
  worktree, or run another plugin's action.
- **Your colors.** It uses your terminal's palette, or Herdr's `[theme]` if you set one.
- **Built for big herds.** It stays light with hundreds of agents and machines; see
  [What it costs](#what-it-costs).

## Install

Requires Herdr 0.9.1+ and Python 3.11+, on macOS or Linux.

```sh
herdr plugin install emanuel2718/herdscope
```

Then bind a key in Herdr's `config.toml` (`prefix+f` is free by default):

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "emanuel2718.herdscope.open"
description = "Herdscope"
```

Herdr runs `python3`. If yours is older than 3.11 (Apple's own is 3.9), Herdscope says so
in the popup.

## Keys

| Key                                   | Action                                                    |
| ------------------------------------- | --------------------------------------------------------- |
| `Enter`, double-click                 | Go to the pane, or run the command                        |
| `↑` `↓`, `Ctrl-N` `Ctrl-P`, wheel     | Move (`PgUp` `PgDn` by a page)                            |
| `Tab` `Shift-Tab`                     | Switch between All, Agents, Terminals and Commands        |
| `>`                                   | Search commands. `Backspace` on an empty search goes back |
| `Ctrl-/`                              | Show or hide the preview                                  |
| `Shift-↑` `Shift-↓`, wheel on preview | Scroll back through the preview                           |
| `Ctrl-X`, then `y`                    | Close the pane                                            |
| `Esc`, `Ctrl-C`                       | Quit, or leave a command's input                          |

The query edits like a shell prompt: `Ctrl-A` `Ctrl-E` `Ctrl-W` `Ctrl-U` `Alt-B` `Alt-F`.

## Search

`sbtrkt` fuzzy · `'wild` exact · `'wild'` exact word · `^music` prefix · `.mp3$` suffix ·
`!fire` exclude · `a | b` either · smart case

Each pane is matched as one line: its label, folder, `workspace:tab`, agent, status and
machine. So `blocked` finds every agent waiting on you. With an empty query, agents come
first, in attention order (blocked, working, done, idle), then terminals. Panes you visit
often rank higher. The pane you came from is tagged `current` and sinks to the bottom.

## Commands

Type `>` from anywhere. Commands act on the pane you opened Herdscope from, its tab and its
workspace, and each row shows your shortcut for it.

- **Workspaces:** new, rename, move up or down, close.
- **Tabs:** new, rename, close.
- **Panes:** split right or down, zoom, rename, close.
- **Agents:** start any agent Herdr supports that you have installed, in a new tab.
- **Also:** new Git worktree, reload Herdr's config, and your other plugins' actions.

Commands ending in `…` ask for a name, and the preview shows the change as you type.
Closing asks for `y` first and lists what will stop.

## Machines

Agents on the machines in `herdr machine list` appear after local panes. Slow or offline
machines never hold up local panes. Until a machine answers, its agents show as Herdscope
last saw them, with their status dimmed.

Each machine refreshes every eight seconds. Machines whose agents haven't changed are asked
less often. With more than 80 machines, refreshes spread out to keep Herdscope to about ten
requests a second in total.

You can preview and focus remote agents, but not remote terminals, and remote panes can't be
closed from here. Herdr has no way for a plugin to switch the machine on screen, so after you
focus a remote agent, Herdscope tells you which machine to pick in the sidebar.

## Colors

Without a `[theme]` in Herdr's config, Herdscope takes your terminal's colors: its text,
background and ANSI palette, with lighter shades mixed from them. The accent is Herdr's, so
it matches the popup border. With a `[theme]`, it follows Herdr's: built-in `name`,
`auto_switch`, and `[theme.custom]` overrides.

## What it costs

With the popup closed, Herdscope runs only two small event hooks:

- **Agent status changes:** a few lines of `sh` that rewrite one small file per pane. That's
  about 3 ms of CPU per change, so 300 agents changing status once a minute use under 2% of
  one core.
- **Pane focus:** a short Python script that counts the visit for ranking. It runs as often as
  you switch panes.

With the popup open:

- **Local panes** refresh once a second over Herdr's socket. Only panes that changed are
  re-matched, and with thousands of panes the refresh slows down to stay under 5% of a core.
- **Previews** read only the selected pane's output.
- **Machines** are asked at most 16 at a time. After the first round, that's about ten
  requests a second at most.
- **Spinners** only cause redraws while one is on screen.

With 1,000 panes, a keystroke takes about 2.5 ms; with 10,000, about 30 ms. With 300 agents,
the open popup uses about 2–4% of one core.

## Development

```sh
herdr plugin link "$PWD"              # run this checkout instead of the installed copy
herdr plugin log list --plugin emanuel2718.herdscope
python3 picker.py list                # the ranked inventory as JSON
python3 -m unittest -v test_picker    # the tests; no dependencies
```

- `open.sh` opens the popup, so the key press doesn't start Python.
- `picker.py` has Herdr's API, the inventory, focusing and closing, and visit history.
- `picker_ui.py` has the view, layout, drawing, input and previews.
- `commands.py` has the command catalog.
- `fuzzy.py` is the fzf port.
- `theme.py` resolves colors.
- `terminal_ui.py` is the terminal canvas, ANSI decoding and input.
- `status.sh` is the status-change hook.
