"""Sign-in, account management, and admin quota control."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .auth import SESSION_TTL
from .context import SESSION_COOKIE
from .deps import context_of, current_user, require_admin
from .domain import User, UserRole
from .pricing import ModelPrice, usd
from .quota import QuotaPolicy, to_micros


class LoginBody(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole = UserRole.MEMBER


class UserUpdate(BaseModel):
    role: UserRole | None = None
    active: bool | None = None
    password: str | None = None


class QuotaBody(BaseModel):
    """Limits in USD. ``None`` means unlimited for that window."""

    budget_usd: str | None = None
    budget_window_hours: int = 720
    burst_usd: str | None = None
    burst_window_hours: int = 5


class CreditBody(BaseModel):
    amount_usd: str
    note: str = "admin credit"


def price_wire(price: ModelPrice) -> dict[str, str]:
    """A price as strings, so no rate is reshaped by JSON float rounding."""

    return {
        "input": str(price.input),
        "cached_input": str(price.cached_input),
        "cache_write": str(price.cache_write),
        "output": str(price.output),
    }


def user_wire(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role.value,
        "active": user.active,
    }


def _amount(raw: str | None, label: str) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise HTTPException(422, f"{label} must be a decimal amount") from None


def router() -> APIRouter:
    api = APIRouter(prefix="/api")

    @api.post("/auth/login")
    async def login(
        request: Request, body: LoginBody, response: Response
    ) -> dict[str, Any]:
        context = context_of(request)
        result = context.auth.login(body.username, body.password)
        if result is None:
            raise HTTPException(401, "invalid username or password")
        user, token = result
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(SESSION_TTL.total_seconds()),
            httponly=True,
            # Browsers drop Secure cookies over plain http (except localhost),
            # so follow the scheme the request actually arrived on: https
            # behind a TLS proxy, http on a trusted LAN.
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return user_wire(user)

    @api.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        context_of(request).auth.logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @api.get("/me")
    async def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return user_wire(user)

    @api.get("/admin/users", dependencies=[Depends(require_admin)])
    async def list_users(request: Request) -> dict[str, Any]:
        context = context_of(request)
        users = context.auth.list_users()
        return {
            "users": [
                {
                    **user_wire(user),
                    "quota": {
                        "policy": context.quota.policy(user.id).to_wire(),
                        "windows": [
                            w.to_wire() for w in context.quota.windows(user.id)
                        ],
                    },
                }
                for user in users
            ]
        }

    @api.post("/admin/users", dependencies=[Depends(require_admin)])
    async def create_user(request: Request, body: UserCreate) -> dict[str, Any]:
        try:
            user = context_of(request).auth.create_user(
                body.username, body.password, body.role
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return user_wire(user)

    @api.patch("/admin/users/{user_id}", dependencies=[Depends(require_admin)])
    async def update_user(
        request: Request, user_id: str, body: UserUpdate
    ) -> dict[str, Any]:
        try:
            user = context_of(request).auth.update_user(
                user_id, role=body.role, active=body.active, password=body.password
            )
        except KeyError:
            raise HTTPException(404, "unknown user") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return user_wire(user)

    @api.put("/admin/users/{user_id}/quota", dependencies=[Depends(require_admin)])
    async def set_quota(
        request: Request, user_id: str, body: QuotaBody
    ) -> dict[str, Any]:
        context = context_of(request)
        if context.auth.get_user(user_id) is None:
            raise HTTPException(404, "unknown user")
        try:
            policy = QuotaPolicy(
                to_micros(_amount(body.budget_usd, "budget_usd")),
                body.budget_window_hours,
                to_micros(_amount(body.burst_usd, "burst_usd")),
                body.burst_window_hours,
            )
            context.quota.set_policy(user_id, policy)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"policy": policy.to_wire()}

    @api.post("/admin/users/{user_id}/credit", dependencies=[Depends(require_admin)])
    async def credit(
        request: Request, user_id: str, body: CreditBody
    ) -> dict[str, Any]:
        context = context_of(request)
        if context.auth.get_user(user_id) is None:
            raise HTTPException(404, "unknown user")
        amount = _amount(body.amount_usd, "amount_usd")
        if amount is None or amount <= 0:
            raise HTTPException(422, "amount_usd must be a positive amount")
        # A credit is a negative ledger row, so it ages out with the window it
        # was granted in rather than lasting forever.
        micros = context.quota.adjust(user_id, -amount, body.note)
        return {
            "credited_usd": str(usd(-micros)),
            "windows": [w.to_wire() for w in context.quota.windows(user_id)],
        }

    @api.get("/admin/prices", dependencies=[Depends(require_admin)])
    async def prices(request: Request) -> dict[str, Any]:
        """The price table in force, so an admin can see what is listed.

        Read-only: prices are edited in ``prices.toml`` on the serving machine,
        which keeps billing rates out of reach of a hijacked browser session.
        """
        context = context_of(request)
        table = context.prices
        return {
            "path": str(context.settings.prices_path),
            "fallback": price_wire(table.fallback),
            "models": [
                {"model": name, **price_wire(price)}
                for name, price in sorted(table.models.items())
            ],
        }

    @api.get("/admin/usage", dependencies=[Depends(require_admin)])
    async def usage(
        request: Request, limit: int = 100, user_id: str | None = None
    ) -> dict[str, Any]:
        context = context_of(request)
        clause = "WHERE l.user_id = ?" if user_id else ""
        params: tuple[Any, ...] = (user_id,) if user_id else ()
        rows = context.database.db.execute(
            f"""SELECT l.*, u.username FROM usage_ledger l
                JOIN users u ON u.id = l.user_id
                {clause}
                ORDER BY l.id DESC LIMIT ?""",
            (*params, max(1, min(limit, 1000))),
        ).fetchall()
        return {
            "entries": [
                {
                    "username": row["username"],
                    "backend": row["backend"],
                    "model": row["model"],
                    "input": row["input_tokens"],
                    "output": row["output_tokens"],
                    "cost_usd": str(usd(row["cost_micros"])),
                    "pricing": row["pricing"],
                    "note": row["note"],
                    "at": row["created_at"],
                }
                for row in rows
            ]
        }

    return api
