from __future__ import annotations

import argparse
import getpass
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import uvicorn

from .app import Settings, create_app, default_data_dir
from .auth import AuthService
from .db import Database
from .domain import UserRole

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _amount(raw: str) -> Decimal | None:
    """Parse a dollar limit; 'none'/'' means unlimited."""

    if raw.strip().lower() in {"", "none", "unlimited"}:
        return None
    return Decimal(raw)


def add_user(data_dir: Path, username: str, role: UserRole) -> int:
    password = getpass.getpass(f"Password for {username}: ")
    if password != getpass.getpass("Repeat password: "):
        print("error: passwords do not match", file=sys.stderr)
        return 2
    database = Database(Settings(data_dir).database_path)
    try:
        user = AuthService(database.db).create_user(username, password, role)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        database.close()
    print(f"created {user.role.value} {user.username}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-chat",
        description="Serve user-managed browser chat for coding agents",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(),
        help="directory holding agentchat.db and prices.toml (default: %(default)s)",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="root for per-user agent workspaces (default: <data-dir>/workspaces)",
    )
    parser.add_argument("--config", type=Path, default=None, help="models.toml path")
    parser.add_argument(
        "--default-budget-usd",
        default="20",
        help="rolling 30-day budget for a user without a policy ('none' to disable)",
    )
    parser.add_argument(
        "--default-burst-usd",
        default="3",
        help="rolling 5-hour burst limit for a user without a policy",
    )
    commands = parser.add_subparsers(dest="command")
    user_cmd = commands.add_parser("add-user", help="create an account")
    user_cmd.add_argument("username")
    user_cmd.add_argument(
        "--role", choices=[role.value for role in UserRole], default="member"
    )
    args = parser.parse_args(argv)
    if args.command == "add-user":
        return add_user(args.data_dir, args.username, UserRole(args.role))
    if args.host not in _LOOPBACK and not args.allow_remote:
        parser.error(
            f"refusing non-loopback host {args.host!r}; pass --allow-remote explicitly"
        )
    try:
        settings = Settings(
            args.data_dir,
            default_budget_usd=_amount(args.default_budget_usd),
            default_burst_usd=_amount(args.default_burst_usd),
            catalog_path=args.config,
            workspace_root=args.workspace_root,
        )
        app = create_app(settings)
    except (ValueError, InvalidOperation) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Agent Chat: http://{args.host}:{args.port}/ (data: {args.data_dir})")
    if app.state.auth.count_users() == 0:
        print(
            "No accounts yet; create one with: agent-chat add-user <name> --role admin"
        )
    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=64 * 1024 * 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
