# Contributing to Herdscope

Thanks for helping. Bug reports, ideas and pull requests are all welcome. For anything larger
than a fix, please open an issue first, so we can agree on the shape of it before you build it.

## Development setup

Herdscope is plain Python 3.11+ and POSIX `sh`, with no dependencies to install.

```sh
git clone https://github.com/emanuel2718/herdscope && cd herdscope
herdr plugin link "$PWD"          # Herdr now runs this checkout; edits apply on the next open
```

Bind a key as the [README](README.md#install) shows, open Herdscope, and you're running your
checkout. Herdr re-reads the manifest live, so there's nothing to restart.

| Command                                                | What it does                                      |
| ------------------------------------------------------ | ------------------------------------------------- |
| `python3 -m unittest`                                  | Run the tests                                     |
| `ruff check . && ruff format --check .`                | Lint and check formatting, as CI does             |
| `python3 -m herdscope list`                            | Print the ranked inventory as JSON                |
| `herdr plugin log list --plugin emanuel2718.herdscope` | See what the hooks and actions ran, and how       |
| `python3 tools/benchmark.py [--fzf]`                   | Time keystrokes and frames; compare with fzf      |
| `python3 tools/screenshot.py`                          | Redraw `assets/screenshot.png` (needs Chrome)     |
| `python3 tools/social_preview.py`                      | Redraw `assets/social-preview.png` (needs Chrome) |

The tests drive the real picker through a pseudo-terminal against a stand-in Herdr, so they
need no running Herdr. If `fzf` is installed, the ranking tests also compare against it.

## How it fits together

| Path                       | What it is                                                                |
| -------------------------- | ------------------------------------------------------------------------- |
| `herdr-plugin.toml`        | The manifest: the action, the popup, and two event hooks                  |
| `scripts/open.sh`          | The action a key runs: opens the popup without starting Python            |
| `scripts/record-status.sh` | The status hook: notes when each agent's status began                     |
| `herdscope/__main__.py`    | Entry points: `ui`, `record-visit`, `list`                                |
| `herdscope/herdr.py`       | Herdr's socket API and CLI, and the environment Herdr runs plugins in     |
| `herdscope/history.py`     | Visit history (frecency) and status times                                 |
| `herdscope/inventory.py`   | Panes as entries, ranking, and the live inventory of every machine        |
| `herdscope/fuzzy.py`       | The fzf port: FuzzyMatchV2 scoring and the extended search syntax         |
| `herdscope/view.py`        | The picker's state, and how keys and clicks change it                     |
| `herdscope/render.py`      | Drawing the list, the preview, and everything around them                 |
| `herdscope/app.py`         | The popup's loop, and the preview reader                                  |
| `herdscope/commands.py`    | The Commands tab's catalog                                                |
| `herdscope/theme.py`       | Colors, from the terminal or Herdr's theme                                |
| `herdscope/terminal.py`    | A small terminal canvas: cells, minimal redraws, ANSI and input decoding  |
| `tests/`                   | One test module per source module, plus end-to-end tests in `test_app.py` |

## Principles

These are what keep Herdscope fast and dependable. Changes should keep them true.

- **Nothing to install.** The standard library only, so `herdr plugin install` is all it takes.
- **Hooks stay tiny.** The status hook runs on every agent's every status change, so it is
  `sh`. The focus hook imports only what it needs. Don't add work to either lightly.
- **The UI thread never waits.** Herdr, the disk and other machines are read on background
  threads; the loop only draws and handles input. A slow machine must never delay local panes.
- **Work only on change.** Typing re-matches only what the query can still match, refreshes
  rebuild only panes that changed, and the terminal is sent only the cells that changed.
- **Measure, don't guess.** Run `python3 tools/benchmark.py` before and after a change to
  matching, the view or drawing, and say what moved in the pull request.
- **Startup is a budget.** The popup should draw within tens of milliseconds of the key press.
  Check `python3 -X importtime -c 'import herdscope.app'` before adding an import to the startup path.
- **Matches fzf.** Ranking and highlighting follow fzf's algorithm, and the tests hold them to
  fzf's own output. A change to `fuzzy.py` should keep `tests/test_fuzzy.py` passing.

## Pull requests

- Keep each pull request to one change, with tests for what it fixes or adds.
- Run the tests and `ruff` before pushing; CI runs both on Linux and macOS.
- Describe user-facing changes under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md).
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org):
  `feat:`, `fix:`, `perf:`, `docs:`, `refactor:`, `test:`, `chore:`.

## Releasing

1. Move the `## [Unreleased]` notes in `CHANGELOG.md` under a new version and date.
2. Set the same `version` in `herdr-plugin.toml`. A test checks the two agree.
3. Commit, tag `vX.Y.Z`, and push the tag. Herdr's marketplace picks up the default branch
   within half an hour; `herdr plugin update` brings it to installed copies.
