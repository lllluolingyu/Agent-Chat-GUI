# Agent Chat

Agent Chat serves browser conversations with Claude Code, Codex, and LingCore
agents to a small group of registered users, adding accounts, per-user
workspaces, and dollar-denominated quotas on top of LingChat's AgentGUI layer.

The product is intentionally chat-only. It does not expose a general purpose
OpenAI-compatible or provider API. A user sends a message through the browser,
the selected backend runs one agent turn, and the server streams the unified
conversation frames back to that user.

## What works today

- accounts with scrypt-hashed passwords, HttpOnly cookie logins, and admin
  user management;
- owner-scoped chat sessions over the AgentGUI browser surface: transcripts,
  approvals, attachments, stop, fork, and the model catalog;
- server-assigned workspaces, one tree per user, selected by name;
- USD quotas on two rolling windows, billed from token counts through an
  operator-owned price table shipped with current Claude, GPT, and Codex rates;
- an admin console at `/admin` for accounts, quota limits, credits, and the
  charge ledger, and a quota meter in the chat topbar for everyone;
- one look across all three pages: the sign-in page and the admin console load
  AgentGUI's stylesheet and self-hosted fonts and reference only its tokens, so
  they follow the chat's theme rather than carrying a second palette;
- one interface language across those pages, Chinese by default with English a
  click away, sharing AgentGUI's own language store so the chat, the console and
  the quota meters never disagree;
- the boundaries and trust model in [`docs/architecture.md`](docs/architecture.md).

## Install and run

This checkout consumes sibling LingCore and LingChat checkouts as editable path
dependencies, so clone all three side by side:

```
LingCore/            # the agent framework
LingCore/LingChat/   # agentgui: the adapters and browser assets
Agent-Chat-GUI/      # this product
```

```bash
uv sync
uv run agent-chat add-user alice --role admin
uv run agent-chat
```

Then open `http://127.0.0.1:8000/` and sign in. State lives in
`~/.local/state/agent-chat/`: `agentchat.db`, `prices.toml`, and `workspaces/`.
Useful flags: `--data-dir`, `--workspace-root`, `--config` (a models.toml
catalog), `--default-budget-usd`, `--default-burst-usd`, `--host`, `--port`,
`--allow-remote`. `AGENT_CHAT_DATA_DIR` sets the data directory's default.

To run it on a server and update it from pushed commits, see
[`docs/deploy.md`](docs/deploy.md).

Provider credentials stay on the server host: the backends use the installed
`claude` and `codex` CLIs and the profiles named in the catalog. No key is ever
sent to a browser or stored in a user record.

## Quotas

Each user has two rolling windows, both in USD, and either may be unlimited:

- a **budget** window, 30 days by default;
- a shorter **burst** window, 5 hours by default.

A turn is checked before it starts. Nothing is reserved, so a turn already
running is allowed to finish even if it crosses the limit and a balance may go
negative; the next turn is refused with `quota_exceeded` and the time the window
recovers. Because the windows roll, a refusal lifts by itself as old spend ages
out. An admin can also grant a credit, which is negative spend that ages out
with the window it was granted in.

Cost is computed from token counts using `prices.toml` (USD per million
tokens), never from an agent's own estimate — the Claude SDK documents
`total_cost_usd` as a client-side estimate that must not bill end users.
Backends differ in what their counters include, and Agent Chat bills each one in
its own convention: Anthropic reports cache reads and writes beside input, while
Codex and LingCore report cached input inside it.

The shipped table lists current Claude, GPT, and Codex rates, and the file's own
header records what it cannot express — long-context and fast-mode tiers, which
under-bill, and batch tiers, which over-bill. Check the rates against your own
bill before relying on them. A model that is not listed is billed at
`[fallback]` and marked `fallback` in the ledger, which the admin console shows
so you can add the missing rate. Only an exact id or a dated snapshot of one
(`claude-opus-5-5-20260901`) counts as listed: a new sibling of a listed model
falls to `[fallback]` rather than quietly inheriting a cheaper relative's price.
Rates are read at startup, so restart the server after editing the file.

Usage is only ever recorded from backend frames. A browser cannot report usage
and cannot override a refusal. For backends that report session running totals
(Claude, Codex) only the increase is billed, so reconnects, resumes, repeated
notifications, and forks do not double-charge.

## Admin console

An admin gets an **Admin** link in the chat topbar, or can open `/admin`
directly. From there they can create accounts, change a role, disable an account,
set a temporary password, edit either quota window, grant a credit, and read the
charge ledger filtered by user. The console also shows which `prices.toml` is in
force; rates themselves are edited on the serving machine, which keeps billing
out of reach of a hijacked browser session.

Every control there calls an admin-only API that re-checks the caller's role, so
hiding a control is a convenience and never the access decision. A member who
reaches `/admin` is sent back to the chat.

## Autonomy

Each session picks one of two levels, which say what the agent may do *without*
asking:

- **ask** — reads freely; every write or command raises an approval you grant
  inline, so nothing is refused on your behalf and no mode switch is needed;
- **edit** — writes inside the workspace happen silently, and stepping outside
  the workspace still asks.

What that means per backend differs, because the backends enforce different
things: Claude uses permission modes, Codex a sandbox, and LingCore a tool
ceiling (it has no per-write approval hook, so under `ask` the write tools are
withheld and an approved shell command is how the agent acts). Sessions created
before this split are renamed on first open — the old `read-only` becomes `ask`
and `auto-edit` becomes `edit`.

## Users and workspaces

Admins create accounts; there is no public sign-up. Every session has one owner,
and a session owned by another user is indistinguishable from a missing one.
Each user gets a workspace tree under the configured root and picks a workspace
by name — the browser never sends a filesystem path, and containment is
re-checked before a backend process starts.

## Test

```bash
uv run pytest -q
uv run ruff check agentchat tests && uv run mypy
```
