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
  operator-owned price table, with admin policy editing and credits;
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
`--allow-remote`.

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
`total_cost_usd` as a client-side estimate that must not bill end users. A model
missing from the table is billed at `[fallback]` and its ledger rows are marked
`fallback`, so add your real models and prices before relying on the numbers.
Backends differ in what their counters include, and Agent Chat bills each one in
its own convention: Anthropic reports cache reads and writes beside input, while
Codex and LingCore report cached input inside it.

Usage is only ever recorded from backend frames. A browser cannot report usage
and cannot override a refusal. For backends that report session running totals
(Claude, Codex) only the increase is billed, so reconnects, resumes, repeated
notifications, and forks do not double-charge.

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
