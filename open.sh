#!/bin/sh
# Opens the `Herdscope` pop.

plugin=${HERDR_PLUGIN_ID:-emanuel2718.herdscope}

out=$("${HERDR_BIN_PATH:-herdr}" plugin pane open --plugin "$plugin" --entrypoint picker \
  --env "HERDSCOPE_PANE=${HERDR_PANE_ID:-}" 2>&1) && exit 0

case $out in
  *"already open"*) exit 0 ;;  # no-op if already opened
esac

printf '%s\n' "$out" >&2

exit 1
