# Security

## What Herdscope can do

Herdscope runs on your machine, as you, like any Herdr plugin. It:

- talks to Herdr over its local socket, and runs the `herdr` CLI to reach machines you have
  added with `herdr machine`;
- reads your Herdr config (`~/.config/herdr/config.toml`) for your theme and key bindings;
- writes only to its own state folder, `~/.local/state/herdr/plugins/emanuel2718.herdscope/`;
- makes no network connections of its own, and sends nothing anywhere.

## Reporting a vulnerability

Please report security issues privately, through
[GitHub's private vulnerability reporting](https://github.com/emanuel2718/herdscope/security/advisories/new),
rather than in a public issue. You can expect a reply within a few days. Once a fix is
released, the advisory is published with credit to you, unless you'd rather not be named.
