# Deploying

The server runs Agent Chat as a systemd user service from plain git checkouts,
and updates by fast-forwarding them to what is pushed. Develop elsewhere;
nothing on the server should be edited in place.

## First install

As the OS user the service will run as (its agents can reach whatever this
user can, see [`architecture.md`](architecture.md#trust-model)):

```bash
cd ~
git clone git@github.com:lllluolingyu/LingCore.git
git clone git@github.com:lllluolingyu/LingChat.git LingCore/LingChat
git clone git@github.com:lllluolingyu/Agent-Chat-GUI.git
cd Agent-Chat-GUI
uv sync --frozen --no-dev
```

The units assume the checkout is `~/Agent-Chat-GUI`; edit their paths if not.
Then:

1. Sign in to `claude` and `codex` as this user; the backends use their
   credentials.
2. `mkdir -p ~/.config/agent-chat && cp deploy/env.example ~/.config/agent-chat/env`
   and set the flags. The file holds absolute paths only.
3. Create the first account: `.venv/bin/agent-chat add-user <name> --role admin`.
   If the env file sets `AGENT_CHAT_DATA_DIR`, pass the same directory with
   `--data-dir`; a shell does not read that file.
4. Install the units and keep them running without a login session:

```bash
systemctl --user enable --now "$PWD/deploy/agent-chat.service"
systemctl --user link "$PWD/deploy/agent-chat-backup.service"
systemctl --user enable --now "$PWD/deploy/agent-chat-backup.timer"
sudo loginctl enable-linger "$USER"
```

Logs: `journalctl --user -u agent-chat`.

## Updating

Push from the development machine: Agent-Chat-GUI, and LingCore or LingChat
too if either changed, since the server runs whatever each checkout's upstream
holds. Then on the server:

```bash
~/Agent-Chat-GUI/deploy/update.sh
```

It checks all three checkouts before touching anything and refuses a checkout
with local changes or one that has diverged from its upstream. Then it stops
the service, snapshots the database, fast-forwards the three, runs
`uv sync --frozen --no-dev`, starts the service, and waits for `/healthz`. It
prints each checkout's commit before and after, and the backup's path.

Doing it by hand is the same steps: `systemctl --user stop agent-chat`,
`deploy/backup.sh pre-update`, `git pull --ff-only` in each checkout,
`uv sync --frozen --no-dev`, `systemctl --user start agent-chat`.

## Backups

`deploy/backup.sh` writes `agentchat.db` (accounts, quotas, the ledger, and chat
history) and `prices.toml` to a timestamped directory under
`~/.local/state/agent-chat-backups/`, keeping the newest 30. The timer runs it
nightly and every update runs it first. It uses SQLite's backup API, so it is
safe while the server runs; a plain `cp` of the database is not. Workspaces are
users' files and not included; cover them and the backup directory with the
machine's own backups.

## Rolling back

Schema migrations run at startup and only go forward, and a build refuses a
database newer than itself. So going back means restoring both the commits and
the database from before the update:

```bash
systemctl --user stop agent-chat
git -C <checkout> checkout <commit>     # each commit update.sh printed
cp <backup>/agentchat.db ~/.local/state/agent-chat/agentchat.db
rm -f ~/.local/state/agent-chat/agentchat.db-wal ~/.local/state/agent-chat/agentchat.db-shm
(cd ~/Agent-Chat-GUI && uv sync --frozen --no-dev)
systemctl --user start agent-chat
```

Anything written after that backup is lost. Once fixed upstream, return each
checkout to its branch (`git checkout main`) before the next update.
