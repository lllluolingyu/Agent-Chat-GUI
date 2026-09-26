"""Owner-scoped chat sessions and the authenticated chat socket."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from agentgui.attachments import content_disposition, display_turns
from agentgui.diagnostics import doctor, live_models
from agentgui.protocol import ApprovalRequest, Decision, frame
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field, StrictInt

from .chat import UserChatConnection
from .context import SESSION_COOKIE, Context
from .deps import context_of, current_user, owned_session, require_admin, same_origin
from .domain import User
from .workspaces import DEFAULT_LABEL, WorkspaceError

_AUTONOMY = {"read-only", "ask", "auto-edit"}


class SessionCreate(BaseModel):
    model_id: str
    # A workspace *name*, resolved under the caller's own root. Never a path.
    workspace: str = DEFAULT_LABEL
    autonomy: str = "ask"


class RenameBody(BaseModel):
    title: str


class ForkBody(BaseModel):
    through_seq: StrictInt | None = Field(default=None, ge=0)


def router() -> APIRouter:
    api = APIRouter(prefix="/api")
    models_state: dict[str, Any] = {"loaded": False, "notice": None}

    @api.get("/models")
    async def models(
        request: Request, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        if not models_state["loaded"]:
            discovered, notice = await live_models()
            for entry in discovered:
                context.catalog.entries.setdefault(entry.id, entry)
            models_state.update(loaded=True, notice=notice)
        labels = context.workspaces.labels(user.id)
        return {
            "models": [entry.to_wire() for entry in context.catalog.entries.values()],
            "notice": models_state["notice"],
            # Workspace *names*; the server maps them to this user's own tree.
            "workspace": labels[0],
            "recent_workspaces": labels,
            "autonomy_levels": sorted(_AUTONOMY),
        }

    @api.get("/doctor", dependencies=[Depends(require_admin)])
    async def health(request: Request) -> dict[str, Any]:
        # Reports host CLI paths and versions, so it stays admin-only.
        return await doctor(context_of(request).catalog)

    @api.get("/me/quota")
    async def my_quota(
        request: Request, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        return {
            "policy": context.quota.policy(user.id).to_wire(),
            "windows": [w.to_wire() for w in context.quota.windows(user.id)],
        }

    @api.get("/sessions")
    async def sessions(
        request: Request, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        owned = {
            row["session_id"]
            for row in context.database.db.execute(
                "SELECT session_id FROM session_owners WHERE user_id = ?", (user.id,)
            )
        }
        listed = [s for s in context.store.list_sessions() if s["id"] in owned]
        return {"enabled": True, "sessions": listed}

    @api.post("/sessions")
    async def create_session(
        request: Request, body: SessionCreate, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        entry = context.catalog.entries.get(body.model_id)
        if entry is None:
            raise HTTPException(422, "unknown model; select an entry from /api/models")
        if body.autonomy not in _AUTONOMY:
            raise HTTPException(422, "invalid autonomy level")
        try:
            workspace = context.workspaces.path(user.id, body.workspace)
        except WorkspaceError as exc:
            raise HTTPException(422, str(exc)) from None
        rec = context.store.create(entry, str(workspace), body.autonomy)
        with context.database.db:
            context.database.db.execute(
                "INSERT INTO session_owners VALUES (?, ?)", (rec.id, user.id)
            )
        return rec.to_wire()

    @api.get("/sessions/{sid}")
    async def transcript(
        request: Request, sid: str, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        rec = owned_session(context, user, sid)
        return {**rec.to_wire(), "turns": display_turns(context.store.turns(sid))}

    @api.patch("/sessions/{sid}")
    async def rename(
        request: Request, sid: str, body: RenameBody, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        rec = owned_session(context, user, sid)
        if not body.title.strip():
            raise HTTPException(422, "title must not be empty")
        rec.title = body.title.strip()[:200]
        context.store.save(rec)
        live = context.attached.get(sid)
        if live:
            live.session.title = rec.title
        return rec.to_wire()

    @api.delete("/sessions/{sid}")
    async def delete(
        request: Request, sid: str, user: User = Depends(current_user)
    ) -> dict[str, bool]:
        context = context_of(request)
        owned_session(context, user, sid)
        if sid in context.attached:
            raise HTTPException(409, "session is open in a connected tab")
        # Ownership and snapshots cascade; ledger rows deliberately survive.
        if not context.store.delete(sid):
            raise HTTPException(404, "unknown session")
        return {"ok": True}

    @api.post("/sessions/{sid}/fork")
    async def fork(
        request: Request, sid: str, body: ForkBody, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        context = context_of(request)
        rec = owned_session(context, user, sid)
        live = context.attached.get(sid)
        if sid in context.attached and (live is None or live.busy or not live.started):
            raise HTTPException(409, "wait for or stop the current turn before forking")
        backend = (
            live.backend
            if live
            else context.backend_factory(rec, context.store, context.profiles)
        )
        if live:
            live.forking = True
        else:
            context.attached[sid] = None

        async def deny(_request: ApprovalRequest) -> Decision:
            return "deny"

        try:
            if not live:
                await backend.start(rec, deny)
            if not backend.capabilities.fork:
                raise ValueError("this backend cannot fork sessions")
            child = await backend.fork(body.through_seq)
        except Exception as exc:
            raise HTTPException(409, str(exc)) from None
        finally:
            if live:
                live.forking = False
            else:
                try:
                    await backend.close()
                finally:
                    context.attached.pop(sid, None)
        with context.database.db:
            context.database.db.execute(
                "INSERT INTO session_owners VALUES (?, ?)", (child.id, user.id)
            )
        # A fork inherits the parent's native agent session, so it must inherit
        # the billing baselines too or its first report looks like fresh spend.
        context.recorder.copy_snapshots(sid, child.id)
        return child.to_wire()

    @api.get("/sessions/{sid}/messages/{seq}/attachments/{index}")
    async def attachment(
        request: Request,
        sid: str,
        seq: int,
        index: int,
        user: User = Depends(current_user),
    ) -> Response:
        context = context_of(request)
        owned_session(context, user, sid)
        for turn in context.store.turns(sid):
            if turn["seq"] != seq:
                continue
            for msg in turn["frames"]:
                items = msg.get("attachments", [])
                if 0 <= index < len(items) and items[index].get("data"):
                    item = items[index]
                    return Response(
                        base64.b64decode(item["data"], validate=True),
                        media_type="application/octet-stream",
                        headers={
                            "Content-Disposition": content_disposition(
                                item.get("name")
                            ),
                            "X-Content-Type-Options": "nosniff",
                            "Cache-Control": "no-store",
                        },
                    )
        raise HTTPException(404, "attachment not found")

    return api


def websocket_route(app: Any) -> None:
    """Register ``/ws``: cookie-authenticated, owner-scoped, one tab per chat."""

    @app.websocket("/ws")
    async def websocket(ws: WebSocket, session: str | None = None) -> None:  # noqa: C901 - one linear handshake
        context: Context = ws.app.state.context
        scheme = "https" if ws.url.scheme in {"https", "wss"} else "http"
        if not same_origin(scheme, ws.headers.get("host"), ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        user = context.auth.authenticate(ws.cookies.get(SESSION_COOKIE))
        if user is None:
            await ws.close(code=4401)
            return
        rec = context.store.get(session) if session else None
        owner = (
            context.database.db.execute(
                "SELECT user_id FROM session_owners WHERE session_id = ?", (rec.id,)
            ).fetchone()
            if rec
            else None
        )
        await ws.accept()
        if rec is None or owner is None or owner["user_id"] != user.id:
            await ws.send_json(
                {"type": "error", "message": "Select New chat or an existing session."}
            )
            await ws.close(code=4404)
            return
        if rec.id in context.attached:
            await ws.send_json({"type": "session_busy", "session": rec.id})
            await ws.close(code=4409)
            return
        context.attached[rec.id] = None
        connection: UserChatConnection | None = None
        try:
            # Re-check containment: the stored path is only as trustworthy as
            # the workspace root it was created under.
            if not context.workspaces.owns(user.id, rec.workspace):
                raise ValueError("this session's workspace is no longer available")
            if not Path(rec.workspace).is_dir():
                raise ValueError("the session workspace no longer exists")
            connection = UserChatConnection(
                ws,
                rec,
                context.store,
                context.backend_factory(rec, context.store, context.profiles),
                user=user,
                quota=context.quota,
                recorder=context.recorder,
            )
            context.attached[rec.id] = connection
            await connection.serve()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                await ws.send_json(frame("error", message=str(exc)).to_wire())
                await ws.close(code=4411)
            except (WebSocketDisconnect, RuntimeError):
                pass
        finally:
            try:
                if connection:
                    await connection.close()
            finally:
                context.attached.pop(rec.id, None)
