# Changelog

All notable changes to Herdscope are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-11-02

### Fixed

- Escape sequences in a folder's name, or in pasted text, reached the terminal, where they
  could change its title or clipboard or garble the screen. They're dropped now.
- A corrupt or unwritable visit history kept the popup from opening. It opens now, and panes
  rank by status alone until the history works again.
- Ranking differed from fzf's for queries with punctuation (`-`, `_`, `.`) and for whole-word
  terms (`'foo'`), and accents folded less than fzf's do: `lodz` didn't find `Łódź`, nor
  `oslo` `Øslo`, nor `abc` fullwidth `ａｂｃ`. All three now match fzf exactly.
- Nerd Font and powerline icons were missing from previews, and the text after them moved left.
- A click in the preview selected the list row on the same line, and a double-click went to it.
- After Herdr restarted, an agent back in the status it had before showed no time in it until
  its status changed.
- Offline machines could keep every connection busy, so machines that answer waited, sometimes
  half a minute. They're now retried a few at a time, and next time they wait their turn.
- Choosing an agent on an offline machine froze the popup for up to twelve seconds. It now
  stays live while the switch happens, and `Esc` cancels it.
- Double-clicking an agent on another machine closed the notice saying which machine to pick
  in Herdr's sidebar before you could read it.
- After a resize, the old layout stayed up for as long as a second.
- A setting of the wrong type in Herdr's config, such as `theme = "dracula"`, crashed the popup.
  It counts as unset now.
- Herdscope's state folder, with your visit history and other machines' task titles, was
  readable by other users on the machine. Only you can read it now.

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

[Unreleased]: https://github.com/emanuel2718/herdscope/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/emanuel2718/herdscope/releases/tag/v0.1.1
[0.1.0]: https://github.com/emanuel2718/herdscope/releases/tag/v0.1.0
