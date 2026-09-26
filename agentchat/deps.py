"""Request-scoped authentication and ownership checks."""

from __future__ import annotations

from urllib.parse import urlsplit

from agentgui.store import SessionRecord
from fastapi import HTTPException, Request

from .context import SESSION_COOKIE, Context
from .domain import User, UserRole


def same_origin(scheme: str, host: str | None, origin: str | None) -> bool:
    """Reject cross-site requests; a missing Origin is a non-browser caller."""

    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme == scheme and parsed.netloc == host


def context_of(request: Request) -> Context:
    context: Context = request.app.state.context
    return context


def current_user(request: Request) -> User:
    context = context_of(request)
    user = context.auth.authenticate(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "sign in required")
    return user


def require_admin(request: Request) -> User:
    user = current_user(request)
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "admin role required")
    return user


def owned_session(context: Context, user: User, sid: str) -> SessionRecord:
    """Return a session this user owns, or fail as if it did not exist.

    A session owned by somebody else must be indistinguishable from a missing
    one, so probing ids cannot reveal another member's chats.
    """
    rec = context.store.get(sid)
    owner = context.database.db.execute(
        "SELECT user_id FROM session_owners WHERE session_id = ?", (sid,)
    ).fetchone()
    if rec is None or owner is None or owner["user_id"] != user.id:
        raise HTTPException(404, "unknown session")
    return rec
