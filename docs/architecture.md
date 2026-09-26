# Agent Chat product boundary

## Goal

Provide one browser application for conversations with Claude Code, Codex, and
LingCore while adding application-level users and quotas. The server owns the
chat session, approval flow, transcript mirror, and quota ledger. It does not
become a general model gateway.

## Trust model

The first deployment target is a trusted group: a family or a small team
sharing one serving machine. Accounts are created by an admin; there is no
public sign-up. Claude Code and Codex run as the server's OS user with API keys
configured on that machine, so every user's agent can reach whatever that OS
user can. Per-user OS isolation (containers or sandboxes) is deferred until the
product is opened beyond a trusted group.

## Reuse of LingChat

The agent adapters, wire protocol, model catalog, attachment validation, and
transcript store are imported from LingChat's `agentgui` package (editable
sibling checkout, see `pyproject.toml`). Agent Chat does not use
`agentgui.server.create_app`: it owns authentication, owner-scoped routes, and
quota checks, and reuses `ChatConnection` by subclassing it. The product tables
live in the same SQLite file as agentgui's `sessions`/`turns` so ownership rows
cascade when a session is deleted.

## User model

- A user has a stable id, unique username, password hash, role, and active flag.
- Roles start with `admin` and `member`.
- Admins create, disable, and reset users; members cannot manage accounts.
- Every chat session has one owner. Session reads and WebSocket attachment are
  checked against that owner before a backend is started.
- Provider and CLI credentials stay on the server host. They are never returned
  to the browser or stored in a user record.

## Quotas

Each user has two rolling windows, both in USD: a budget window (30 days by
default) and a shorter burst window (5 hours). Either may be unlimited. Amounts
are stored as micro-dollars so SQLite sums stay exact.

A turn is checked before it starts and billed while it runs. Nothing is
reserved: an in-flight turn is never cut off, so a balance may go negative and
the next turn is refused with the time its window recovers. Rolling windows mean
a refusal lifts without any scheduled reset. Admin credits are negative ledger
rows, so they age out with the window they were granted in.

Cost comes from token counts through an operator-owned price table, never from
an agent's own estimate. Each backend is priced in its own counter convention
(`pricing.CACHED_INSIDE_INPUT`): Anthropic reports cache reads and writes beside
input, Codex and LingCore report cached input inside it. An unlisted model is
billed at the fallback price and flagged in the ledger rather than running free.

Backends that report session running totals (Claude's `model_usage`, Codex's
thread totals) are billed on the increase over a stored per-(session, model)
snapshot, so a reconnect, a resumed session, or a repeated notification charges
nothing. A counter that moves backwards means the agent process restarted, so
the new values are fresh spend. A fork inherits its parent's snapshots, because
it also inherits the parent's native agent session.

Quota checks are server-side and fail closed: an unreadable ledger refuses the
turn. The browser may display remaining quota, but it cannot supply usage values
or override a denial. Ledger rows outlive the chat they came from; snapshots and
ownership rows cascade with it.

## Chat boundary

The only model operation exposed to users is an authenticated chat turn over
the browser WebSocket. There is no `/v1/chat/completions`, embeddings route,
provider proxy, or arbitrary tool execution endpoint. Backend adapters are
invoked by the chat service after ownership, backend availability, and quota
checks pass.

## Delivered slices

1. SQLite migrations plus password and cookie-session authentication.
2. Owner-scoped session routes, the chat socket, and admin user management.
3. The LingChat AgentGUI layer, reused as a dependency rather than ported:
   adapters, protocol, catalog, attachments, transcript store, and browser
   assets. `UserChatConnection` subclasses `agentgui.server.ChatConnection` to
   add the quota check and billing; `agentgui.server.create_app` is not used,
   because this product owns authentication and routing.
4. The price table, usage ledger, rolling-window checks, and admin quota
   editing plus credits.

## Still open

- Per-user OS isolation (containers or sandboxes) before opening this beyond a
  trusted group.
- A team or family budget shared across users, above the per-user limits.
- Audit events for admin actions, and a browser view of a user's own ledger.
- LingCore turns are billed only when its profile's provider returns usage;
  a provider that reports none yields no charge.

## Security decisions to keep explicit

- Default binding remains loopback; remote deployment requires an explicit
  operator flag and a TLS/reverse-proxy boundary.
- Passwords use a slow password hash with per-user salts; sessions use rotated,
  HttpOnly, Secure, SameSite cookies.
- User workspaces are isolated under a configured root and validated before a
  backend process starts.
- Agent approvals belong to the authenticated user/session that opened the
  chat. A response from another user must never resolve that approval future:
  the approval registry is per connection, and one session accepts one live
  socket at a time.
- The browser never names a filesystem path. A workspace is chosen by name,
  resolved under the caller's own root, and re-checked for containment before a
  backend process starts.
- Usage is recorded from backend events and never trusted from client frames.

