from collections.abc import Callable

from fastapi.testclient import TestClient

SignIn = Callable[[str], None]


def test_login_page_is_public_and_app_shell_requires_a_session(
    client: TestClient,
) -> None:
    page = client.get("/login")
    assert page.status_code == 200 and "Sign in" in page.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_page_styling_is_reachable_before_signing_in(
    client: TestClient,
) -> None:
    """It borrows AgentGUI's tokens and fonts, so both must be public."""

    page = client.get("/login")
    assert "/style.css" in page.text and "/app/agentchat.css" in page.text
    for asset in ("/style.css", "/app/agentchat.css"):
        assert client.get(asset).status_code == 200, asset


def test_signed_in_page_loads_the_overlay_before_the_app_module(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    html = client.get("/").text
    assert html.index("/app/overlay.js") < html.index("/js/main.js")
    assert "/app/agentchat.css" in html
    # AgentGUI's own assets are served unchanged beside it.
    assert client.get("/js/main.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    for asset in ("overlay.js", "quota.js", "agentchat.css"):
        assert client.get(f"/app/{asset}").status_code == 200, asset
    assert "quota" in client.get("/app/overlay.js").text
    # Only the named assets are reachable, so the route cannot be walked.
    assert client.get("/app/admin.html").status_code == 404
    assert client.get("/app/../pricing.py").status_code == 404


def test_admin_console_is_served_only_to_an_admin(
    client: TestClient, sign_in: SignIn
) -> None:
    console = client.get("/admin", follow_redirects=False)
    assert console.status_code == 303 and console.headers["location"] == "/login"
    sign_in("alice")
    member = client.get("/admin", follow_redirects=False)
    # A member has no admin API to call, so they go back to the chat.
    assert member.status_code == 303 and member.headers["location"] == "/"
    sign_in("admin")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Add a user" in page.text and "/app/admin.js" in page.text
