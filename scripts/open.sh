#!/bin/sh
# The "open" action: opens Herdscope's popup, passing on the pane it was opened from.
#
# It is sh so a key press costs one herdr call, not a Python start. Pressing the key again
# while the popup is open is not an error.

plugin=${HERDR_PLUGIN_ID:-emanuel2718.herdscope}

out=$("${HERDR_BIN_PATH:-herdr}" plugin pane open --plugin "$plugin" --entrypoint picker \
  --env "HERDSCOPE_PANE=${HERDR_PANE_ID:-}" 2>&1) && exit 0

case $out in
  *"already open"*) exit 0 ;;
esac

printf '%s\n' "$out" >&2
exit 1
