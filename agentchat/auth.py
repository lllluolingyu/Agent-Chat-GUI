"""Password hashing, user accounts, and cookie-backed login sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .domain import User, UserRole

# scrypt parameters: ~32 MiB and tens of milliseconds per check. Stored in the
# hash so they can be raised later without invalidating existing passwords.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
SESSION_TTL = timedelta(days=14)
MIN_PASSWORD_LENGTH = 8


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified when a username is unknown so login timing does not reveal accounts.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], UserRole(row["role"]), bool(row["active"]))


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


class AuthService:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def create_user(
        self, username: str, password: str, role: UserRole = UserRole.MEMBER
    ) -> User:
        username = username.strip()
        if not username or len(username) > 64:
            raise ValueError("username must be 1-64 characters")
        validate_password(password)
        user = User(uuid4().hex, username, role)
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?,?)",
                    (
                        user.id,
                        user.username,
                        hash_password(password),
                        user.role.value,
                        1,
                        _now().isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError("username is already taken") from None
        return user

    def get_user(self, user_id: str) -> User | None:
        row = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self.db.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(row) for row in rows]

    def count_users(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def update_user(
        self,
        user_id: str,
        *,
        role: UserRole | None = None,
        active: bool | None = None,
        password: str | None = None,
    ) -> User:
        user = self.get_user(user_id)
        if user is None:
            raise KeyError(user_id)
        if password is not None:
            validate_password(password)
        with self.db:
            if role is not None or active is False:
                self._keep_an_admin(user, role, active)
            if role is not None:
                self.db.execute(
                    "UPDATE users SET role = ? WHERE id = ?", (role.value, user_id)
                )
            if active is not None:
                self.db.execute(
                    "UPDATE users SET active = ? WHERE id = ?", (int(active), user_id)
                )
            if password is not None:
                self.db.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(password), user_id),
                )
            if active is False or password is not None:
                # Disabling an account or resetting its password ends every login.
                self.db.execute(
                    "DELETE FROM auth_sessions WHERE user_id = ?", (user_id,)
                )
        updated = self.get_user(user_id)
        assert updated is not None
        return updated

    def _keep_an_admin(
        self, user: User, role: UserRole | None, active: bool | None
    ) -> None:
        demoted = role is not None and role != UserRole.ADMIN
        if user.role != UserRole.ADMIN or not (demoted or active is False):
            return
        others = self.db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
            (user.id,),
        ).fetchone()[0]
        if not others:
            raise ValueError("at least one active admin is required")

    def login(self, username: str, password: str) -> tuple[User, str] | None:
        """Return the user and a new session token, or ``None`` on any failure."""

        row = self.db.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        if row is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, row["password_hash"]) or not row["active"]:
            return None
        token = secrets.token_urlsafe(32)
        now = _now()
        with self.db:
            self.db.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            self.db.execute(
                "INSERT INTO auth_sessions VALUES (?,?,?,?)",
                (
                    _token_hash(token),
                    row["id"],
                    now.isoformat(),
                    (now + SESSION_TTL).isoformat(),
                ),
            )
        return _user(row), token

    def authenticate(self, token: str | None) -> User | None:
        if not token:
            return None
        row = self.db.execute(
            """SELECT users.* FROM auth_sessions
               JOIN users ON users.id = auth_sessions.user_id
               WHERE token_hash = ? AND expires_at > ? AND users.active = 1""",
            (_token_hash(token), _now().isoformat()),
        ).fetchone()
        return _user(row) if row else None

    def logout(self, token: str | None) -> None:
        if token:
            with self.db:
                self.db.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?",
                    (_token_hash(token),),
                )
