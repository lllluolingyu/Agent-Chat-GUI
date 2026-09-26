"""Server-assigned workspaces, one directory tree per user.

The browser never names a filesystem path. A user picks a workspace by label;
the server resolves it under that user's own root and verifies containment
before an agent process is started, so one member's agent cannot be pointed at
another member's files (or anywhere else on the host) by a crafted request.
"""

from __future__ import annotations

import re
from pathlib import Path

# Deliberately strict: a label is a directory name, never a path.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULT_LABEL = "default"


class WorkspaceError(ValueError):
    pass


def validate_label(label: str) -> str:
    label = label.strip() or DEFAULT_LABEL
    if not _LABEL.match(label) or label in {".", ".."}:
        raise WorkspaceError(
            "a workspace name may use letters, digits, dot, dash, and underscore"
        )
    return label


class Workspaces:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def user_root(self, user_id: str) -> Path:
        return self.root / user_id

    def path(self, user_id: str, label: str) -> Path:
        """Resolve a label to a directory, creating it on first use."""

        user_root = self.user_root(user_id)
        target = (user_root / validate_label(label)).resolve()
        base = user_root.resolve() if user_root.exists() else user_root
        # resolve() collapses symlinks and "..", so containment is checked on
        # the real path an agent would be started in.
        if not target.is_relative_to(base):
            raise WorkspaceError("workspace path escapes the user's workspace root")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def labels(self, user_id: str) -> list[str]:
        user_root = self.user_root(user_id)
        if not user_root.is_dir():
            return [DEFAULT_LABEL]
        found = sorted(
            entry.name
            for entry in user_root.iterdir()
            if entry.is_dir() and not entry.is_symlink() and _LABEL.match(entry.name)
        )
        return found or [DEFAULT_LABEL]

    def owns(self, user_id: str, path: str) -> bool:
        """True when ``path`` is inside this user's root (for a stored session)."""

        user_root = self.user_root(user_id)
        try:
            base = user_root.resolve()
            return Path(path).resolve().is_relative_to(base)
        except OSError:
            return False
