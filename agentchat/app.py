"""The application factory.

The browser assets are LingChat's AgentGUI single page, served from the
installed package with one overlay script injected: this product authenticates
with a cookie instead of a launch token, assigns workspaces by name, and shows
the signed-in user plus their remaining quota. Reusing the page keeps one
transcript/approval surface instead of a second divergent copy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from agentgui.catalog import Catalog
from agentgui.server import BackendFactory, make_backend
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import routes_admin, routes_chat
from .context import SESSION_COOKIE, Context, Settings, default_data_dir
from .deps import same_origin

__all__ = ["create_app", "Settings", "default_data_dir", "SESSION_COOKIE"]

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WEB_DIR = Path(__file__).with_name("web")
_MAIN_SCRIPT = '<script type="module" src="/js/main.js"></script>'
# Injected *before* the app module: the overlay wraps WebSocket, so it has to
# run first. Module scripts execute in document order.
_OVERLAY = '<script type="module" src="/overlay.js"></script>\n  ' + _MAIN_SCRIPT

_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Sign in · Agent Chat</title>
  <style>
    body{font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;
         background:#0f1115;color:#e7e9ee}
    form{display:grid;gap:.75rem;width:min(22rem,90vw);padding:2rem;border-radius:1rem;
         background:#171a21;box-shadow:0 1px 0 #262b36 inset}
    h1{font-size:1.25rem;margin:0 0 .5rem}
    label{display:grid;gap:.35rem;font-size:.85rem;color:#9aa3b2}
    input{font:inherit;padding:.55rem .7rem;border-radius:.5rem;border:1px solid #2b313d;
          background:#0f1115;color:inherit}
    button{font:inherit;padding:.6rem;border-radius:.5rem;border:0;background:#6366f1;color:#fff;
           cursor:pointer}
    p[role=alert]{margin:0;color:#f87171;font-size:.85rem;min-height:1.2em}
  </style>
</head>
<body>
  <form id="login">
    <h1>Agent Chat</h1>
    <label>Username <input name="username" autocomplete="username" required autofocus /></label>
    <label>Password <input name="password" type="password" autocomplete="current-password" required /></label>
    <button type="submit">Sign in</button>
    <p role="alert" id="error"></p>
  </form>
  <script>
    document.getElementById("login").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      const error = document.getElementById("error");
      error.textContent = "";
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
        }),
      });
      if (response.ok) { location.href = "/"; return; }
      const data = await response.json().catch(() => ({}));
      error.textContent = typeof data.detail === "string" ? data.detail : "Sign-in failed.";
    });
  </script>
</body>
</html>"""


def agentgui_web_dir() -> Path:
    """Locate the AgentGUI browser assets inside the installed package."""

    import agentgui

    return Path(agentgui.__file__).with_name("web")


def index_html() -> str:
    """AgentGUI's page with this product's overlay script appended."""

    html = (agentgui_web_dir() / "index.html").read_text(encoding="utf-8")
    if _MAIN_SCRIPT not in html:  # pragma: no cover - upstream shape changed
        raise RuntimeError("agentgui index.html no longer loads /js/main.js")
    return html.replace(_MAIN_SCRIPT, _OVERLAY, 1)


def create_app(
    settings: Settings | None = None,
    *,
    catalog: Catalog | None = None,
    backend_factory: BackendFactory = make_backend,
) -> FastAPI:
    settings = settings or Settings(default_data_dir())
    context = Context.build(settings, catalog=catalog, backend_factory=backend_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await context.close()

    app = FastAPI(title="Agent Chat", version="0.1.0", lifespan=lifespan)
    app.state.context = context
    # Kept for tests and callers that reach for the services directly.
    app.state.settings = settings
    app.state.auth = context.auth
    app.state.quota = context.quota

    @app.middleware("http")
    async def origin_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in _UNSAFE_METHODS and not same_origin(
            request.url.scheme,
            request.headers.get("host"),
            request.headers.get("origin"),
        ):
            return JSONResponse({"detail": "cross-origin request refused"}, 403)
        return await call_next(request)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page() -> str:
        return _LOGIN_PAGE

    @app.get("/overlay.js")
    async def overlay() -> FileResponse:
        return FileResponse(
            _WEB_DIR / "overlay.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(routes_admin.router())
    app.include_router(routes_chat.router())
    routes_chat.websocket_route(app)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        if context.auth.authenticate(request.cookies.get(SESSION_COOKIE)) is None:
            return Response(status_code=303, headers={"Location": "/login"})
        return HTMLResponse(index_html(), headers={"Referrer-Policy": "no-referrer"})

    # Mounted last so the routes above win; serves AgentGUI's css/js unchanged.
    app.mount("/", StaticFiles(directory=agentgui_web_dir()), name="static")
    return app
