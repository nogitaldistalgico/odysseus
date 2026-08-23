"""OpenCode transparent proxy routes.

Provides three endpoint groups:

1. **SSE event stream** ``GET /api/opencode/event`` — explicit route for clean
   SSE headers (matched before the catch-all).
2. **Catch-all HTTP proxy** ``/api/opencode/{path}`` — every other opencode
   REST endpoint is forwarded 1:1.
3. **Local config** ``/api/opencode-config/*`` — Odysseus-side configuration
   (project list, health, proxy reload) that is **not** forwarded to opencode.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.responses import StreamingResponse

log = logging.getLogger("opencode.routes")


def setup_opencode_routes():
    """Factory — returns ``(proxy_router, config_router)``."""

    from src.auth import require_user

    router = APIRouter(prefix="/api/opencode", tags=["opencode"])
    config_router = APIRouter(prefix="/api/opencode-config", tags=["opencode-config"])

    # ------------------------------------------------------------------
    #  Lazy proxy singleton — created on first request from settings.
    # ------------------------------------------------------------------

    _proxy: Optional["OpenCodeProxy"] = None  # noqa: F821

    def _get_proxy():
        nonlocal _proxy
        if _proxy is not None:
            return _proxy

        from src.settings import get_setting

        url = get_setting("opencode_url", "")
        if not url:
            raise HTTPException(
                status_code=503,
                detail="opencode integration is not configured.  Set opencode_url in Settings → Integrations.",
            )

        from services.opencode_proxy import OpenCodeProxy

        _proxy = OpenCodeProxy(
            base_url=url,
            username=get_setting("opencode_username", "opencode"),
            password=get_setting("opencode_password", ""),
        )
        return _proxy

    def _invalidate_proxy():
        nonlocal _proxy
        _proxy = None

    # ==================================================================
    #  1) SSE global event stream  (explicit — matched BEFORE catch-all)
    # ==================================================================

    @router.get("/event")
    async def proxy_event_stream(request: Request):
        """Transparent SSE proxy to opencode's global event bus."""
        require_user(request)
        proxy = _get_proxy()
        result = await proxy.proxy_stream(request, "event")
        if result is None:
            raise HTTPException(502, "opencode server is unreachable")
        upstream, client = result

        async def _stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            except Exception:
                log.debug("SSE upstream closed", exc_info=True)
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ==================================================================
    #  2) Catch-all HTTP proxy
    # ==================================================================

    @router.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_catchall(request: Request, path: str):
        """Forward any opencode REST request transparently."""
        require_user(request)
        proxy = _get_proxy()

        content_type_hint = request.headers.get("accept", "")
        # Heuristic: if the client expects SSE or the path is known to stream,
        # use the streaming code-path.  For everything else, read-then-return
        # avoids keeping an httpx client alive longer than needed.
        _STREAMING_PATHS = (
            "event",
            "message",      # POST /session/:id/message (synchronous prompt)
        )
        is_stream = (
            "text/event-stream" in content_type_hint
            or any(path.rstrip("/").endswith(sp) for sp in _STREAMING_PATHS)
            # POST to prompt_async is NOT streaming (returns 204 immediately)
            # but the synchronous /message endpoint is.
        )

        if is_stream:
            result = await proxy.proxy_stream(request, path)
            if result is None:
                raise HTTPException(502, "opencode server is unreachable")
            upstream, client = result

            ct = upstream.headers.get("content-type", "application/json")
            is_sse = "text/event-stream" in ct

            async def _stream():
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                except Exception:
                    log.debug("upstream stream closed", exc_info=True)
                finally:
                    await upstream.aclose()
                    await client.aclose()

            headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"} if is_sse else {}
            return StreamingResponse(
                _stream(),
                status_code=upstream.status_code,
                media_type=ct.split(";")[0].strip(),
                headers=headers,
            )

        # ── Non-streaming: read full body, close, return ──
        result = await proxy.proxy_read(request, path)
        if result is None:
            raise HTTPException(502, "opencode server is unreachable")
        body, status, ct = result
        return Response(
            content=body,
            status_code=status,
            media_type=ct.split(";")[0].strip() if ct else "application/json",
        )

    # ==================================================================
    #  3) Local configuration (Odysseus-side, NOT proxied)
    # ==================================================================

    @config_router.get("")
    async def get_opencode_config(request: Request):
        """Return Odysseus-side opencode configuration."""
        require_user(request)
        from src.settings import get_setting

        return {
            "enabled": bool(get_setting("opencode_url", "")),
            "url": get_setting("opencode_url", ""),
            "projects": get_setting("opencode_projects", []),
        }

    @config_router.post("/projects")
    async def set_opencode_projects(request: Request):
        """Update the list of project directories."""
        require_user(request)
        from src.settings import load_settings, save_settings

        body = await request.json()
        settings = load_settings()
        settings["opencode_projects"] = body.get("projects", [])
        save_settings(settings)
        return {"ok": True}

    @config_router.get("/health")
    async def opencode_health(request: Request):
        """Probe opencode server connectivity."""
        require_user(request)
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
        require_user(request)
        _invalidate_proxy()
        return {"ok": True, "detail": "Proxy will reconnect on next request."}

    return router, config_router
