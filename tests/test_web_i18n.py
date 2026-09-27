"""This product's interface strings and their wiring.

The table is JavaScript registered into AgentGUI's i18n module, so a typo'd key
reaches the browser as a raw `ac.col_user` where a column header should be. These
read the table and its call sites as text and check they agree.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

WEB = Path(__file__).resolve().parent.parent / "agentchat" / "web"
STRINGS = WEB / "strings.js"

_ENTRY_START = re.compile(r'^  "(ac\.[\w.]*)": \{', re.M)


def _declared() -> dict[str, str]:
    text = STRINGS.read_text(encoding="utf-8")
    starts = list(_ENTRY_START.finditer(text))
    assert starts, "no strings parsed out of strings.js"
    return {
        m.group(1): text[
            m.end() : (starts[i + 1].start() if i + 1 < len(starts) else len(text))
        ]
        for i, m in enumerate(starts)
    }


def test_every_string_is_translated_into_every_language() -> None:
    for key, body in _declared().items():
        for lang in ("en", "zh"):
            assert f"{lang}:" in body, f"{key} has no {lang} translation"


def test_product_keys_are_namespaced() -> None:
    """`ac.` prefixes keep an upstream string from being shadowed by ours."""

    text = STRINGS.read_text(encoding="utf-8")
    body = text[text.index("register({") :]
    for key in re.findall(r'^  "([\w.]+)":', body, re.M):
        assert key.startswith("ac."), f"{key} is not namespaced under ac."


def test_every_referenced_key_exists() -> None:
    declared = set(_declared())
    missing: list[str] = []
    for source in sorted(WEB.glob("*.js")):
        text = source.read_text(encoding="utf-8")
        # `(?<![\w$])` keeps this off the tail of createElement("div").
        for key in re.findall(r'(?<![\w$])t\(\s*"(ac\.[\w.]*)"', text):
            if key not in declared:
                missing.append(f"{source.name}: {key}")
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    for key in re.findall(r'data-i18n(?:-[a-z-]+)?="(ac\.[\w.]*)"', admin):
        if key not in declared:
            missing.append(f"admin.html: {key}")
    assert not missing, f"keys used but never declared: {missing}"


def test_pages_share_one_language_store_with_agentgui() -> None:
    """One choice, one toggle: these pages read AgentGUI's key, not their own."""

    for name in ("strings.js", "overlay.js", "admin.js", "quota.js"):
        text = (WEB / name).read_text(encoding="utf-8")
        assert '"/js/i18n.js"' in text, f"{name} does not use AgentGUI's i18n module"
    admin = (WEB / "admin.html").read_text(encoding="utf-8")
    assert "agentgui-lang" in admin and '<html lang="zh">' in admin


def test_login_page_is_self_contained_and_defaults_to_chinese() -> None:
    """The sign-in form must not wait on another module to render.

    Importing AgentGUI's i18n module here would make a failed asset load a
    lockout, so the page carries its own few strings and reads the same key.
    """

    from agentchat.app import _LOGIN_PAGE

    assert "/js/i18n.js" not in _LOGIN_PAGE
    assert "agentgui-lang" in _LOGIN_PAGE
    assert '<html lang="zh">' in _LOGIN_PAGE
    assert 'zh: "登录"' in _LOGIN_PAGE


def test_product_strings_are_served(client: TestClient) -> None:
    response = client.get("/app/strings.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "ac.window_budget" in response.text
