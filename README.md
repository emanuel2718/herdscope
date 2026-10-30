# Herdscope

A fuzzy finder for everything in [Herdr](https://herdr.dev), with live previews

```

  ❯ search                                                                 9/9     ● Migrate router to typed routes
                                                                                     codex  ·  web:3  ·  ~/code/web
  All 9    Agents 5    Terminals 4    Commands 16
  ━━━━━───────────────────────────────────────────────────────────────────────
                                                                                     apps/web/src/router.ts +38 -21
▌ ● Migrate router to typed routes              codex     web:3    blocked  2m        12 - const routes = [
  ● Fix flaky checkout e2e test                 claude    web:1    done     6m        12 + const routeTree = rootRoute.addChildren([
  ⠙ Review error handling in handlers           opencode  api:1    working  3m        13 +   homeRoute, projectsRoute, settingsRoute,
  ⠙ Add rate limiting to the public API         claude    api:2    working 12m        14 + ])
  ● Where are the fonts loaded?                 claude    web:2    idle     1h
                                                                                     Allow Codex to edit apps/web/src/router.ts?
  › ~/code/web                                            web:4                      › 1. Yes, proceed
  › lazygit  ~/code/api                                   api:3                        2. Yes, and don't ask again for this file
  › nvim inbox.md  ~/notes                                notes:1                      3. No, tell Codex what to do differently esc
  › pnpm dev  current  ~/code/web                         web:4



  ↵ go   ⇥ scope   > commands   ^/ preview   ⇧↑↓ scroll   ^x close   esc quit
```

- **Every agent and terminal in one list.** Agents show their task, place and
  status, with how long it has held: `blocked 2m`, `done 6m`. Terminals show the
  running command and its folder.
- **Live previews.** The selected pane's own output, colors included, updated
  as it changes.
- **fzf's matching.** A port of fzf's algorithm and search syntax, accent
  handling included, so results rank and highlight like fzf's. The tests compare
  it with fzf itself. fzf doesn't need to be installed.
- **Herdr commands.** Type `>` to search commands: new and rename workspace,
  tabs, splits, zoom, worktrees, starting agents, and other plugins' actions.
- **Your terminal's colors.** No theme to configure: it reads your terminal's
  background and palette, light or dark.
- **Fast.** The key press opens the popup in about 20 ms, the first frame
  follows in under 100 ms, and nothing moves while you type.

## Install

Requires Herdr 0.9.1 or later and Python 3.11 or later, on macOS or Linux.
Herdr runs `python3`; if that's older (Apple's own is 3.9), Herdscope says so in
the popup.

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

## Keys

| Keys                                                               | Action                                                    |
| ------------------------------------------------------------------ | --------------------------------------------------------- |
| `Enter`, `Ctrl-Y`, double-click                                    | Go to the selected pane, or run the selected command      |
| `Ctrl-N`/`Ctrl-P`, `Ctrl-J`/`Ctrl-K`, `↓`/`↑`, wheel               | Move                                                      |
| `PgDn`/`PgUp`                                                      | Move a page                                               |
| `Tab`/`Shift-Tab`, click a tab                                     | All / Agents / Terminals / Commands                       |
| `>`                                                                | Search commands; `Backspace` on an empty search goes back |
| `Ctrl-/`, `Alt-P`                                                  | Toggle the preview                                        |
| `Shift-↑`/`Shift-↓`, `Shift-PgUp`/`Shift-PgDn`, wheel over preview | Scroll the preview back through output                    |
| `Ctrl-X`, then `y`                                                 | Close the selected pane (any other key cancels)           |
| `Esc`, `Ctrl-C`, `Ctrl-G`, `Ctrl-Q`                                | Quit, or leave a command's input                          |
| `Ctrl-A`/`E`/`B`/`F`, `Alt-B`/`F`, `Ctrl-U`/`W`, `Alt-D`           | Edit the query, readline-style                            |

## Commands

```

  > run a command                                                        16/16     ◫ Split right
                                                                                     pane  ·  pnpm dev  ·  ⌃B v
  All 9    Agents 5    Terminals 4    Commands 16
  ────────────────────────────────────━━━━━━━━━━━─────────────────────────────     Splits the pane you came from and starts a shell beside it.

  + New workspace…                                  workspace            ⌃B ⇧N ┃   ╭──────────────────────────────╮┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐
  ✎ Rename workspace…                               workspace  web       ⌃B ⇧W ┃   │                              │╎                              ╎
  ↓ Move workspace down                             workspace  web             ┃   │                              │╎                              ╎
  × Close workspace                                 workspace  web       ⌃B ⇧D ┃   │                              │╎                              ╎
                                                                               ┃   │                              │╎                              ╎
  + New tab…                                        tab        web       ⌃B c  ┃   │                              │╎                              ╎
  ✎ Rename tab…                                     tab        web:4     ⌃B ⇧T ┃   │           pnpm dev           │╎           new pane           ╎
  × Close tab                                       tab        web:4     ⌃B ⇧X ┃   │                              │╎                              ╎
                                                                               ┃   │                              │╎                              ╎
▌ ◫ Split right                                     pane       pnpm dev  ⌃B v  ┃   │                              │╎                              ╎
  ⊟ Split down                                      pane       pnpm dev  ⌃B -  ┃   │                              │╎                              ╎
  ⤢ Zoom pane                                       pane       pnpm dev  ⌃B z  ┃   ╰──────────────────────────────╯└╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘
  ✎ Rename pane…                                    pane       pnpm dev  ⌃B ⇧P
  × Close pane                                      pane       pnpm dev  ⌃B x

  ▶ Start claude                                    agent      web

  ↵ run   ⇥ scope   ⌫ back to panes   esc quit
```

Type `>` from anywhere, or `Tab` to the Commands tab. The `>` becomes the prompt
symbol rather than part of the query.

- Commands act on where you opened Herdscope from: that pane, its tab and its
  workspace. Each row names its target and shows your shortcut for it, taken
  from Herdr's config.
- Commands ending in `…` ask for text next. The prompt becomes an input,
  starting from the current name when renaming, and the preview shows the
  change as you type. `Enter` applies it; `Esc` goes back.
- Closing a workspace, tab or pane asks for `y` first and lists what will stop.
  Splits preview the new layout.
- Search knows a few other words: `space` finds workspace commands, `window`
  finds tab ones, `kill` finds the close commands.

The list: new, rename, move up or down, and close a workspace; new, rename and
close a tab; split right or down, zoom, rename and close a pane; start any agent
Herdr supports that's installed, each in a new tab (except Cursor, whose `cursor`
command is usually the editor); new Git worktree; reload Herdr's config; and the
actions of your other Herdr plugins.

## Search

`sbtrkt` fuzzy · `'wild` exact · `'wild'` exact word · `^music` prefix ·
`.mp3$` suffix · `!fire` exclude · `a | b` either · smart case.

Each pane is searched as one line: label, directory, `workspace:tab`, agent,
status, path and machine, so `blocked` finds the agents waiting on you. With an
empty query, agents come first in attention order (blocked, working, done,
idle), then terminals; within each, panes you visit often and recently rank
higher (frecency with a seven-day half-life). The pane you are on is tagged
`current` and sinks to the bottom of its group.

## What you see

- The list sits on your terminal's background, like Herdr's sidebar; the
  preview is one step lighter, and the selected row takes the preview's tone.
  Below about 100 columns the preview moves under the list.
- An agent's status time comes from a `pane.agent_status_changed` hook that
  records when each status began. It stays blank until a change has been seen.
- Nothing moves while you type: tabs keep their places, counts are padded, and
  column widths come from every pane rather than just the matches.
- Empty tabs and searches say so: "No agents running", "No matches for …".

## Colors

With no `[theme]` in Herdr's config, Herdscope uses the terminal's colors:

- Text, the list's background and every hue are the terminal's own defaults and
  ANSI palette, so they match exactly and follow theme switches.
- The lighter preview tone and the greys are mixed from the terminal's
  background and text colors, which it asks the terminal for when it opens (the
  standard OSC 10/11 queries; Herdr answers them). A terminal that doesn't
  answer gets its own default colors, dimmed text for the greys, and a line
  between the list and the preview.
- The accent is Herdr's (`[ui] accent` applies), matching the popup border.

Set a `[theme]` in Herdr's config and Herdscope follows it instead: any of
Herdr's built-in themes in `name`, `auto_switch` with `dark_name`/`light_name`
(chosen by the terminal's background), and `[theme.custom]` token overrides,
with Herdr's own token names.

## Machines

Enabled machines from `herdr machine list` are listed beside local panes and
refresh every eight seconds; slow or offline machines never hold up local
results and appear after terminals with the error. Remote agents can be
previewed and focused. Herdr has no plugin API to switch the displayed machine,
so after focusing a remote agent Herdscope tells you which machine to pick in
the sidebar. Remote terminals are omitted and remote panes can't be closed from
here.

## Development

| File             | Role                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------ |
| `open.sh`        | The `open` action: asks Herdr to open the popup, without starting Python on the key press                    |
| `picker.py`      | Herdr API, inventory threads, focus and close, visit and status history (SQLite in `HERDR_PLUGIN_STATE_DIR`) |
| `picker_ui.py`   | View state, layout, drawing, input handling, preview fetching                                                |
| `commands.py`    | The command catalog: targets, shortcuts and the API call behind each command                                 |
| `fuzzy.py`       | The fzf matcher port                                                                                         |
| `theme.py`       | Terminal-derived colors, Herdr theme palettes and config resolution                                          |
| `terminal_ui.py` | True-color canvas with row diffing, ANSI decoding, color queries, key, mouse and paste input                 |

```sh
herdr plugin link "$PWD"              # use a checkout instead of the installed copy
herdr plugin log list --plugin emanuel2718.herdscope
python3 picker.py list                # the ranked inventory as JSON
python3 -m unittest -v test_picker    # the tests; no dependencies
```
