#!/bin/sh
# The pane.agent_status_changed hook: remembers when each agent's status began, for "blocked 2m".
#
# It runs on every status change of every agent, so it stays in sh rather than starting Python:
# a file per pane holds its status and is rewritten only when that changes, so the file's
# modification time is when the status began. The picker reads these; the pane.focused hook
# deletes those of panes that are gone.
#
# Herdr's event JSON is compact, as in {"pane_id":"w1:p2",...,"agent_status":"blocked"}.

event=${HERDR_PLUGIN_EVENT_JSON:-}
pane=${event#*\"pane_id\":\"}
pane=${pane%%\"*}
status=${event#*\"agent_status\":\"}
status=${status%%\"*}

case $status in idle | working | blocked | done | unknown) ;; *) exit 0 ;; esac
case $pane in '' | *[!A-Za-z0-9:_-]*) exit 0 ;; esac # It names a file.

# Plugin state is shared by every Herdr session, so each socket gets its own folder.
socket=${HERDR_SOCKET_PATH:-$HOME/.config/herdr/herdr.sock}
dir=${HERDR_PLUGIN_STATE_DIR:?}/status$socket
file=$dir/$pane

# A repeat keeps the time it began, unless the file is from before Herdr last started: the
# picker ignores those, since the pane ID may have named another pane then.
old=
[ -f "$file" ] && read -r old < "$file"
# -nt is in POSIX.1-2024, and dash, bash and busybox sh have it, though they differ on a
# missing file: without the socket, keep the time.
# shellcheck disable=SC3013
[ "$old" = "$status" ] && { [ ! -e "$socket" ] || [ "$file" -nt "$socket" ]; } && exit 0

[ -d "$dir" ] || mkdir -p "$dir"
printf '%s\n' "$status" > "$file"
