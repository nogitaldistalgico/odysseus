"""OpenCode transparent proxy routes.

Provides three endpoint groups:

1. **SSE event stream** ``GET /api/opencode/event`` — explicit route for clean
   SSE headers (matched before the catch-all).
2. **Catch-all HTTP proxy** ``/api/opencode/{path}`` — every other opencode
   REST endpoint is forwarded 1:1.
3. **Local config** ``/api/opencode-config/*`` — Odysseus-side configuration
   (project list, health, proxy reload) that is **not** forwarded to opencode.

Access is gated twice: the ``can_use_opencode`` privilege (opencode exposes
shell and file-edit tools on its host, so this is as powerful as
``can_use_bash``), and — when the caller has configured a project list — an
allowlist check on the requested directory.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.responses import StreamingResponse

log = logging.getLogger("opencode.routes")

# Set by setup_opencode_routes so app shutdown can release pooled connections.
_close_hooks: list = []


async def close_opencode_proxy() -> None:
    """Close the pooled upstream client. Safe to call when never configured."""
    for hook in _close_hooks:
        try:
            await hook()
        except Exception:
            log.debug("opencode proxy close failed", exc_info=True)


def _allowed_roots(request: Request, user: str) -> list[str]:
    """Project directories this caller may address, or [] for 'unrestricted'.

    Mirrors the resolution order of ``GET /api/opencode-config``: the user's own
    project list wins, the global setting is the fallback. An empty list means
    no allowlist has been configured, which stays permissive so existing setups
    keep working.
    """
    from src.settings import get_setting
    from routes.prefs_routes import _load_for_user

    try:
        projects = _load_for_user(user).get("opencode_projects")
    except Exception:
        projects = None
    if projects is None:
        projects = get_setting("opencode_projects", [])
    if not isinstance(projects, list):
        return []
    return [os.path.normpath(p) for p in projects if isinstance(p, str) and p.strip()]


def _requested_directories(request: Request) -> list[str]:
    """Every directory the caller is asking opencode to operate on.

    opencode reads the project directory from a ``directory`` query param (v1
    routes), a ``location[directory]`` query param (``/api/*`` routes) or the
    ``x-opencode-directory`` header — so all three have to be checked.
    """
    from urllib.parse import unquote

    found: list[str] = []
    for key in ("directory", "location[directory]"):
        val = request.query_params.get(key)
        if val:
            found.append(val)
    hdr = request.headers.get("x-opencode-directory")
    if hdr:
        found.append(unquote(hdr))
    return found


def _enforce_directory_allowlist(request: Request, user: str) -> None:
    """403 when the caller addresses a directory outside their project list."""
    roots = _allowed_roots(request, user)
    if not roots:
        return
    for raw in _requested_directories(request):
        candidate = os.path.normpath(raw)
        if not any(
            candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)
            for root in roots
        ):
            log.warning("opencode: user %r blocked from directory %r", user, candidate)
            raise HTTPException(
                status_code=403,
                detail="That directory is not in your configured opencode projects.",
            )


def setup_opencode_routes():
    """Factory — returns ``(proxy_router, config_router)``."""

    from src.auth_helpers import require_user_api_aware, require_privilege_api_aware

    router = APIRouter(prefix="/api/opencode", tags=["opencode"])
    config_router = APIRouter(prefix="/api/opencode-config", tags=["opencode-config"])

    # ------------------------------------------------------------------
    #  Proxy singleton, keyed on the settings it was built from so an admin
    #  editing the URL takes effect without an explicit /reload or restart.
    # ------------------------------------------------------------------

    _proxy: Optional["OpenCodeProxy"] = None  # noqa: F821
    _proxy_key: Optional[tuple] = None

    def _settings_key() -> tuple:
        from src.settings import get_setting

        return (
            get_setting("opencode_url", ""),
            get_setting("opencode_username", "opencode"),
            get_setting("opencode_password", ""),
        )

    def _get_proxy():
        nonlocal _proxy, _proxy_key

        key = _settings_key()
        if not key[0]:
            raise HTTPException(
                status_code=503,
                detail="opencode integration is not configured.  Set opencode_url in Settings → Integrations.",
            )
        if _proxy is not None and _proxy_key == key:
            return _proxy

        from services.opencode_proxy import OpenCodeProxy

        stale = _proxy
        _proxy = OpenCodeProxy(base_url=key[0], username=key[1], password=key[2])
        _proxy_key = key
        if stale is not None:
            import asyncio

            asyncio.create_task(stale.aclose())
        return _proxy

    def _invalidate_proxy():
        nonlocal _proxy, _proxy_key
        stale, _proxy, _proxy_key = _proxy, None, None
        if stale is not None:
            import asyncio

            asyncio.create_task(stale.aclose())

    async def _close_proxy():
        nonlocal _proxy, _proxy_key
        stale, _proxy, _proxy_key = _proxy, None, None
        if stale is not None:
            await stale.aclose()

    _close_hooks.append(_close_proxy)

    def _authorize(request: Request) -> str:
        """Privilege gate + project allowlist. Returns the resolved user."""
        user = require_privilege_api_aware(request, "can_use_opencode")
        _enforce_directory_allowlist(request, user)
        return user

    # ------------------------------------------------------------------
    #  Shared forwarding
    # ------------------------------------------------------------------

    async def _forward(request: Request, path: str):
        """Proxy one request, streaming only what is genuinely a stream.

        The old code guessed from the *request* path whether to stream, which
        sent ``GET /session/{id}/message`` (a plain JSON list) out chunked and
        without a Content-Length — so an upstream failure mid-body reached the
        client as truncated JSON under a 200.  The upstream *response*
        content-type is the only reliable signal, so we open the response
        first and decide from there.
        """
        from services.opencode_proxy import OpenCodeUnreachable

        proxy = _get_proxy()
        try:
            upstream = await proxy.open(request, path)
        except OpenCodeUnreachable as exc:
            raise HTTPException(502, f"opencode server is unreachable: {exc}") from exc

        ct = upstream.headers.get("content-type", "application/json")
        headers = proxy.response_headers(upstream)

        if "text/event-stream" not in ct:
            try:
                body = await upstream.aread()
            except Exception as exc:
                log.warning("opencode response read failed for %s: %r", path, exc)
                raise HTTPException(502, "opencode response was truncated") from exc
            finally:
                await upstream.aclose()
            headers.pop("content-type", None)
            return Response(
                content=body,
                status_code=upstream.status_code,
                media_type=ct,
                headers=headers,
            )

        # ── SSE: forward chunks untouched until either side hangs up ──
        headers.update(
            {
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
            }
        )
        headers.pop("content-type", None)

        async def _stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            except Exception:
                log.debug("SSE upstream closed", exc_info=True)
            finally:
                await upstream.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers=headers,
        )

    # ==================================================================
    #  1) SSE global event stream  (explicit — matched BEFORE catch-all)
    # ==================================================================

    @router.get("/event")
    async def proxy_event_stream(request: Request):
        """Transparent SSE proxy to opencode's global event bus.

        opencode filters this stream by the instance directory, so callers must
        pass ``?directory=`` (EventSource cannot set the header form).
        """
        _authorize(request)
        return await _forward(request, "event")

    # ==================================================================
    #  2) Catch-all HTTP proxy
    # ==================================================================

    @router.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def proxy_catchall(request: Request, path: str):
        """Forward any opencode REST request transparently."""
        _authorize(request)
        return await _forward(request, path)

    # ==================================================================
    #  3) Local configuration (Odysseus-side, NOT proxied)
    # ==================================================================

    @config_router.get("")
    async def get_opencode_config(request: Request):
        """Return Odysseus-side opencode configuration."""
        user = require_user_api_aware(request)
        from src.settings import get_setting
        from routes.prefs_routes import _load_for_user

        prefs = _load_for_user(user)
        # Fallback to global settings if user hasn't set personal projects
        projects = prefs.get("opencode_projects")
        if projects is None:
            projects = get_setting("opencode_projects", [])

        # Lets the UI hide the Code rail entry instead of failing on first use.
        allowed = True
        try:
            require_privilege_api_aware(request, "can_use_opencode")
        except HTTPException:
            allowed = False

        return {
            "enabled": bool(get_setting("opencode_url", "")),
            "allowed": allowed,
            "url": get_setting("opencode_url", ""),
            "projects": projects,
        }

    @config_router.post("/projects")
    async def set_opencode_projects(request: Request):
        """Update the list of project directories (user-specific)."""
        user = require_user_api_aware(request)
        from routes.prefs_routes import _load_for_user, _save_for_user

        body = await request.json()
        prefs = _load_for_user(user)
        prefs["opencode_projects"] = body.get("projects", [])
        _save_for_user(user, prefs)
        return {"ok": True}

    @config_router.get("/health")
    async def opencode_health(request: Request):
        """Probe opencode server connectivity."""
        require_user_api_aware(request)
        try:
            proxy = _get_proxy()
        except HTTPException:
            return JSONResponse(
                {"ok": False, "error": "not configured"},
                status_code=503,
            )
        return await proxy.health_check()

    @config_router.post("/reload")
    async def reload_opencode_proxy(request: Request):
        """Drop cached proxy so next request re-reads settings."""
        require_user_api_aware(request)
        _invalidate_proxy()
        return {"ok": True, "detail": "Proxy will reconnect on next request."}

    return router, config_router
