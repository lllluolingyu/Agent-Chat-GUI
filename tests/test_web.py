from collections.abc import Callable

from fastapi.testclient import TestClient

SignIn = Callable[[str], None]


def test_login_page_is_public_and_app_shell_requires_a_session(
    client: TestClient,
) -> None:
    page = client.get("/login")
    assert page.status_code == 200 and "Sign in" in page.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_signed_in_page_loads_the_overlay_before_the_app_module(
    client: TestClient, sign_in: SignIn
) -> None:
    sign_in("alice")
    html = client.get("/").text
    assert html.index("/overlay.js") < html.index("/js/main.js")
    # AgentGUI's own assets are served unchanged beside it.
    assert client.get("/js/main.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    overlay = client.get("/overlay.js")
    assert overlay.status_code == 200
    assert "quota" in overlay.text
