#!/usr/bin/env bash
# Snapshot Agent Chat's database and price table into a timestamped directory
# and print its path. Safe while the server runs: SQLite's backup API copies a
# consistent state, where a plain cp of a WAL database may not.
#
# Workspaces hold users' files and can be large; cover them with the machine's
# own backups.
#
# Usage: backup.sh [label]
set -euo pipefail
shopt -s nullglob

# Settings shared with the systemd units: KEY=value lines, parsed rather than
# sourced so they mean what systemd's EnvironmentFile makes of them. A
# variable already in the environment wins.
env_file=$HOME/.config/agent-chat/env
if [[ -f $env_file ]]; then
    while IFS='=' read -r key value || [[ -n $key ]]; do
        [[ $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ && ! -v $key ]] || continue
        [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]] && value=${BASH_REMATCH[1]}
        export "$key=$value"
    done <"$env_file"
fi

app=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
state=${XDG_STATE_HOME:-$HOME/.local/state}
data=${AGENT_CHAT_DATA_DIR:-$state/agent-chat}
backups=${AGENT_CHAT_BACKUP_DIR:-$state/agent-chat-backups}
keep=${AGENT_CHAT_BACKUP_KEEP:-30}

if [[ ! -f $data/agentchat.db ]]; then
    echo "error: no database in $data" >&2
    exit 1
fi
dest=$backups/$(date +%Y%m%d-%H%M%S)${1:+-$1}
mkdir -p "$dest"
"$app/.venv/bin/python" - "$data/agentchat.db" "$dest/agentchat.db" <<'EOF'
import sqlite3
import sys

src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    sys.exit("error: backup failed its integrity check")
EOF
if [[ -f $data/prices.toml ]]; then
    cp -p "$data/prices.toml" "$dest/"
fi

# Keep the newest $keep snapshots. The names start with a timestamp, so glob
# order is age order, and nothing else in the directory is touched.
snapshots=("$backups"/[0-9]*-[0-9]*/)
if ((${#snapshots[@]} > keep)); then
    rm -rf -- "${snapshots[@]:0:${#snapshots[@]}-keep}"
fi
echo "$dest"
