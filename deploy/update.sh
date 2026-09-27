#!/usr/bin/env bash
# Update a deployed Agent Chat: stop the service, back up the database,
# fast-forward all three checkouts to their upstreams, and start it again.
# Run on the server as the service's user.
#
# The checkouts must sit side by side as in development:
#   <root>/LingCore  <root>/LingCore/LingChat  <root>/Agent-Chat-GUI
set -euo pipefail

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
root=$(dirname "$app")
repos=("$root/LingCore" "$root/LingCore/LingChat" "$app")
url=${AGENT_CHAT_URL:-http://127.0.0.1:8000}

die() {
    echo "error: $*" >&2
    exit 1
}

healthy() {
    "$app/.venv/bin/python" -c \
        'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2)' \
        "$url/healthz" 2>/dev/null
}

# Wrapped in a function so bash parses all of it before the merge below can
# replace this file.
main() {
    # Fetch and check everything while the server still runs, so a network
    # error or a diverged checkout never leaves it stopped.
    local repo
    declare -A before
    for repo in "${repos[@]}"; do
        git -C "$repo" fetch --quiet
        [[ -z $(git -C "$repo" status --porcelain --untracked-files=no) ]] ||
            die "$repo has local changes"
        git -C "$repo" merge-base --is-ancestor HEAD '@{u}' ||
            die "$repo has diverged from its upstream"
        before[$repo]=$(git -C "$repo" rev-parse --short HEAD)
    done

    systemctl --user stop agent-chat
    # Migrations only go forward and an older build refuses a newer schema, so
    # this snapshot is what makes going back possible.
    local backup
    if ! backup=$("$app/deploy/backup.sh" pre-update); then
        systemctl --user start agent-chat
        die "backup failed; nothing was updated"
    fi
    echo "backup: $backup"
    for repo in "${repos[@]}"; do
        echo "${repo#"$root"/}: ${before[$repo]}"
    done
    trap 'echo "update failed; roll back to the commits and backup above" \
"(docs/deploy.md, Rolling back)" >&2' EXIT

    for repo in "${repos[@]}"; do
        git -C "$repo" merge --ff-only --quiet '@{u}'
    done
    (cd "$app" && uv sync --frozen --no-dev --quiet)
    systemctl --user daemon-reload
    systemctl --user start agent-chat

    local _
    for _ in $(seq 30); do
        if healthy; then
            trap - EXIT
            for repo in "${repos[@]}"; do
                echo "${repo#"$root"/}: ${before[$repo]} -> $(git -C "$repo" rev-parse --short HEAD)"
            done
            echo "Agent Chat is up at $url"
            return 0
        fi
        sleep 1
    done
    die "no answer from $url/healthz after 30s; check: journalctl --user -u agent-chat"
}

main "$@"
