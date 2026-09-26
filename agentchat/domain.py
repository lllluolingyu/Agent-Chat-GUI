"""Product domain types shared by auth, chat, and quota services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    role: UserRole
    active: bool = True
