"""Thin transparent proxy to a remote opencode server.

All HTTP methods, query parameters, request bodies, and response bodies are
forwarded as-is.  The proxy only adds:

  1. Odysseus authentication (validated by AuthMiddleware before we run)
  2. opencode Basic Auth header injection
  3. Streaming passthrough for SSE and chunked responses

No request or response is interpreted, transformed, or cached.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Optional, Tuple

import httpx

log = logging.getLogger("opencode.proxy")

# ---------------------------------------------------------------------------
#  Timeouts — generous on the *read* side because LLM generation can take
#  minutes, strict on *connect* so a dead upstream fails fast.
# ---------------------------------------------------------------------------
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


class OpenCodeProxy:
    """Stateless HTTP/SSE proxy to an ``opencode serve`` instance.

    Each public method either returns raw *httpx* response objects (for
    streaming) or pre-read bytes.  The caller (the route layer) is responsible
    for wrapping them in the appropriate FastAPI ``Response`` subclass.
    """

    def __init__(
        self,
        base_url: str,
        username: str = "opencode",
        password: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth_header = self._make_basic_auth(username, password)
        log.info("OpenCode proxy initialised → %s", self.base_url)

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_basic_auth(username: str, password: str) -> str:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {creds}"

    def _upstream_headers(self, request: Any) -> dict[str, str]:
        """Build headers for the upstream opencode request.

        Only a curated set of headers is forwarded to avoid leaking
        Odysseus-internal tokens or cookies to the upstream server.
        """
        headers: dict[str, str] = {"Authorization": self._auth_header}
        _FORWARD = (
            "content-type",
            "accept",
            "x-opencode-directory",
        )
        for key in _FORWARD:
            val = request.headers.get(key)
            if val:
                headers[key] = val
        return headers

    # ------------------------------------------------------------------
    #  Proxy entry-points
    # ------------------------------------------------------------------

    async def proxy_stream(
        self,
        request: Any,
        path: str,
    ) -> Optional[Tuple[httpx.Response, httpx.AsyncClient]]:
        """Open a *streaming* connection to upstream and return it.

        Returns ``(upstream_response, client)`` on success so the caller can
        iterate over the body and close both when done.  Returns ``None`` if
        the upstream is unreachable (caller should return 502).
        """
        target = f"{self.base_url}/{path}"
        qs = str(request.query_params) if request.query_params else None
        body = await request.body()

        client = httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            upstream = await client.send(
                client.build_request(
                    method=request.method,
                    url=target,
                    params=qs,
                    content=body if body else None,
                    headers=self._upstream_headers(request),
                ),
                stream=True,
            )
            return upstream, client
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            log.warning("opencode unreachable at %s: %s", target, exc)
            await client.aclose()
            return None

    async def proxy_read(
        self,
        request: Any,
        path: str,
    ) -> Optional[Tuple[bytes, int, str]]:
        """Proxy a request and read the full response body.

        Returns ``(body_bytes, status_code, content_type)`` or ``None`` if
        upstream is unreachable.
        """
        target = f"{self.base_url}/{path}"
        qs = str(request.query_params) if request.query_params else None
        body = await request.body()

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(
                    method=request.method,
                    url=target,
                    params=qs,
                    content=body if body else None,
                    headers=self._upstream_headers(request),
                )
                return (
                    resp.content,
                    resp.status_code,
                    resp.headers.get("content-type", "application/json"),
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            log.warning("opencode unreachable at %s: %s", target, exc)
            return None

    # ------------------------------------------------------------------
    #  Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Quick connectivity probe against ``/health``."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(
                    f"{self.base_url}/health",
                    headers={"Authorization": self._auth_header},
                )
                return {"ok": r.status_code == 200, "status": r.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
