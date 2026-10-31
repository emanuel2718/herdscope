# Changelog

All notable changes to Herdscope are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-31

The first release.

### Added

- One fuzzy-searchable list of every agent and terminal in Herdr, with agents ranked by what
  needs you: blocked, working, done, then idle, and how long each has been that way.
- A live preview of the selected pane's output, in its own colors, with scrollback.
- fzf's matching and search syntax, ported and tested against fzf itself, with no dependency
  on it: fuzzy, exact, prefix, suffix, inverse and either terms, smart case and accent folding.
- Frecency: panes you visit often and recently rank higher among equals.
- Agents on other machines (`herdr machine list`), shown from a cache while they connect, and
  refreshed at a pace that scales to hundreds of machines.
- A Commands tab (`>`): new, rename, reorder and close workspaces and tabs; split, zoom,
  rename and close panes; start any installed agent; create a Git worktree; reload Herdr's
  config; run other plugins' actions. Each shows your key binding and previews its effect.
- Closing panes from the list (`Ctrl-X`), confirmed first.
- A help overlay with every key and the search syntax (`?` on an empty search, or `F1`).
- Colors from your terminal's palette, or from Herdr's `[theme]`, including its built-in
  themes, `auto_switch` and `[theme.custom]`.
- Full mouse support: click, double-click, wheel, and tabs.

### Performance

- The popup draws its first frame about 45 ms after the key press, most of it Python starting.
- With 1,000 panes a keystroke takes about 3 ms; with 10,000, about 21 ms.
- With the popup closed, only two small hooks run: a few lines of `sh` per agent status change,
  and a short Python script per pane focus.

[Unreleased]: https://github.com/emanuel2718/herdscope/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/emanuel2718/herdscope/releases/tag/v0.1.0
