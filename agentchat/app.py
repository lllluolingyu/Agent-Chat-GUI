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
from .domain import UserRole

__all__ = ["create_app", "Settings", "default_data_dir", "SESSION_COOKIE"]

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WEB_DIR = Path(__file__).with_name("web")
# This product's own assets, served under /app/ so they never collide with a
# future AgentGUI file. Listed explicitly rather than mounted: the directory
# also holds the admin page, which is served only to an admin.
_ASSETS = {
    "overlay.js": "text/javascript",
    "quota.js": "text/javascript",
    "admin.js": "text/javascript",
    "agentchat.css": "text/css",
}
_MAIN_SCRIPT = '<script type="module" src="/js/main.js"></script>'
# Injected *before* the app module: the overlay wraps WebSocket, so it has to
# run first. Module scripts execute in document order.
_OVERLAY = (
    '<link rel="stylesheet" href="/app/agentchat.css" />\n  '
    '<script type="module" src="/app/overlay.js"></script>\n  ' + _MAIN_SCRIPT
)

_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Sign in · Agent Chat</title>
  <!-- AgentGUI's tokens and self-hosted fonts, so the sign-in page is the same
       material as the chat behind it rather than a second hand-rolled palette. -->
  <link rel="stylesheet" href="/style.css" />
  <link rel="stylesheet" href="/app/agentchat.css" />
  <style>
    /* style.css lays the chat shell out as a flex row pinned to the viewport;
       this page is one centred card on a document that may scroll. */
    body{display:grid;place-items:center;height:auto;min-height:100vh;min-height:100dvh;
         overflow:visible}
    /* Solid and hairlined, like the composer it will hand over to: there is
       nothing behind this card to blur. */
    form{display:grid;gap:.85rem;width:min(23rem,90vw);padding:2rem;
         border:1px solid var(--border-strong);border-radius:var(--radius-xl);
         background:var(--surface-input);box-shadow:var(--glass-shadow)}
    h1{font-family:var(--serif);font-size:1.6rem;font-weight:400;letter-spacing:-.01em;
       margin:0 0 .35rem}
    label{display:grid;gap:.35rem;font-size:.8rem;font-weight:600;color:var(--text-2)}
    input{font:inherit;padding:.6rem .75rem;border-radius:var(--radius-sm);
          border:1px solid var(--border-strong);background:var(--glass-bg-subtle);color:var(--text)}
    input:focus-visible{outline:none;border-color:color-mix(in srgb,var(--accent) 50%,var(--border-strong));
                        box-shadow:var(--ring-focus)}
    button{font:inherit;font-weight:620;padding:.65rem;border-radius:var(--radius-sm);border:0;
           background:var(--accent-strong);color:#fff;cursor:pointer}
    button:hover{filter:brightness(1.06)}
    p[role=alert]{margin:0;color:var(--err);font-size:.82rem;min-height:1.2em}
  </style>
  <script>
    // Same pre-paint theme read as the chat page, so signing in does not flash
    // dark and then settle light (or the reverse).
    (() => {
      try {
        const t = localStorage.getItem("agentgui-theme");
        if (t === "light" || (t === null && matchMedia("(prefers-color-scheme: light)").matches)) {
          document.documentElement.dataset.theme = "light";
        }
      } catch { /* storage may be unavailable; the default theme is fine */ }
    })();
  </script>
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

    @app.get("/app/{asset}")
    async def product_asset(asset: str) -> Response:
        media_type = _ASSETS.get(asset)
        if media_type is None:
            return JSONResponse({"detail": "unknown asset"}, 404)
        return FileResponse(
            _WEB_DIR / asset,
            media_type=media_type,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> Response:
        user = context.auth.authenticate(request.cookies.get(SESSION_COOKIE))
        if user is None:
            return Response(status_code=303, headers={"Location": "/login"})
        # A member has no admin API to call, so send them back to the chat
        # rather than serving a console whose every request would fail.
        if user.role is not UserRole.ADMIN:
            return Response(status_code=303, headers={"Location": "/"})
        return HTMLResponse(
            (_WEB_DIR / "admin.html").read_text(encoding="utf-8"),
            headers={"Referrer-Policy": "no-referrer"},
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
