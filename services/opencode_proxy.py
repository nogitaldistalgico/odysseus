"""Thin transparent proxy to a remote opencode server.

All HTTP methods, query parameters, request bodies, response bodies, and
response headers are forwarded as-is.  The proxy only adds:

  1. Odysseus authentication (validated by AuthMiddleware before we run)
  2. opencode Basic Auth header injection
  3. Streaming passthrough for SSE

No request or response is interpreted, transformed, or cached.
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("opencode.proxy")

# ---------------------------------------------------------------------------
#  Timeouts — generous on the *read* side because LLM generation can take
#  minutes, strict on *connect* so a dead upstream fails fast.  SSE streams get
#  no read timeout at all: an idle event bus is normal, not a failure.
# ---------------------------------------------------------------------------
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
_SSE_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)

# A single pooled client per proxy instance. Building an AsyncClient per request
# means a fresh TCP (and TLS) handshake for every call, which a chatty client
# like the iOS app pays for on every screen.
_LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=16)

# Headers we must not copy from the upstream response: hop-by-hop headers, and
# anything Starlette recomputes for the response we build.  `content-encoding`
# is critical — httpx has already decoded the body, so forwarding the original
# encoding would tell the client to decompress plaintext.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-encoding",
        "content-length",
        "date",
        "server",
    }
)


class OpenCodeUnreachable(Exception):
    """Upstream opencode server could not be reached or spoke bad HTTP."""


def normalize_base_url(url: str) -> str:
    """Return ``url`` with a scheme and no trailing slash.

    An operator typing ``10.0.0.5:4096`` into Settings would otherwise reach
    httpx as a bare authority and raise ``UnsupportedProtocol`` deep inside the
    request path, surfacing as an opaque 500.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not urlsplit(url).scheme:
        url = f"http://{url}"
    return url


class OpenCodeProxy:
    """Stateless HTTP/SSE proxy to an ``opencode serve`` instance.

    ``open()`` returns a *streaming* ``httpx.Response``; the caller decides
    whether to drain it (normal JSON) or forward it chunk-by-chunk (SSE), and
    owns closing it either way.
    """

    def __init__(
        self,
        base_url: str,
        username: str = "opencode",
        password: str = "",
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._auth_header = self._make_basic_auth(username, password)
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=_LIMITS,
            follow_redirects=False,
        )
        log.info("OpenCode proxy initialised → %s", self.base_url)

    async def aclose(self) -> None:
        """Release the pooled connections. Called on app shutdown / reload."""
        try:
            await self._client.aclose()
        except Exception:
            log.debug("closing opencode client failed", exc_info=True)

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_basic_auth(username: str, password: str) -> str:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {creds}"

    @staticmethod
    def _passthrough(value: str) -> bytes:
        """Restore the exact bytes a header arrived as.

        Starlette decodes incoming header bytes as latin-1, while httpx encodes
        outgoing ones as *ascii* with no fallback — so handing a decoded string
        straight through raises ``UnicodeEncodeError`` (HTTP 500) for any
        project path containing an umlaut.  Re-encoding as latin-1 reproduces
        the original bytes, which is what a proxy owes the upstream anyway.
        Clients that need to be certain (opencode decodeURIComponent's
        ``x-opencode-directory`` on its /api/* routes) should percent-encode;
        that survives this untouched.
        """
        try:
            return value.encode("latin-1")
        except UnicodeEncodeError:
            return value.encode("utf-8", "replace")

    def _upstream_headers(self, request: Any) -> dict[str, bytes]:
        """Build headers for the upstream opencode request.

        Only a curated set is forwarded, so Odysseus session cookies and bearer
        tokens never reach the upstream server.
        """
        headers: dict[str, bytes] = {"Authorization": self._auth_header.encode()}
        _FORWARD = (
            "content-type",
            "accept",
            # opencode resolves the project/workspace from these (see
            # packages/server/src/location.ts). Without the workspace header
            # forwarded, workspace selection is impossible through the proxy.
            "x-opencode-directory",
            "x-opencode-workspace",
        )
        for key in _FORWARD:
            val = request.headers.get(key)
            if val:
                headers[key] = self._passthrough(val)
        return headers

    @staticmethod
    def response_headers(upstream: httpx.Response) -> dict[str, str]:
        """Copy the upstream response headers minus hop-by-hop ones.

        Dropping everything but content-type used to lose `content-disposition`
        on downloads, `www-authenticate` on 401s and `retry-after` on 429s.
        """
        out: dict[str, str] = {}
        for key, value in upstream.headers.multi_items():
            key = key.lower()
            if key in _HOP_BY_HOP:
                continue
            out[key] = value
        return out

    @staticmethod
    def _is_sse_request(request: Any, path: str) -> bool:
        """Whether to drop the read timeout for this request.

        Only affects timeout selection — the *response* content-type decides how
        the body is actually forwarded, so guessing wrong here is harmless.
        """
        if "text/event-stream" in (request.headers.get("accept") or ""):
            return True
        return path.rstrip("/").endswith("event")

    # ------------------------------------------------------------------
    #  Proxy entry-point
    # ------------------------------------------------------------------

    async def open(self, request: Any, path: str) -> httpx.Response:
        """Send the request upstream and return the *unread* response.

        The caller must ``aclose()`` the returned response. Raises
        :class:`OpenCodeUnreachable` when the upstream cannot be talked to.
        """
        target = f"{self.base_url}/{path.lstrip('/')}"
        qs = str(request.query_params) if request.query_params else None
        body = await request.body()
        if not body and request.method in ("POST", "PUT", "PATCH"):
            # Send an explicit empty body so Content-Length: 0 is set, rather
            # than a bodyless POST some servers reject.
            body = b""

        try:
            return await self._client.send(
                self._client.build_request(
                    method=request.method,
                    url=target,
                    params=qs,
                    content=body,
                    headers=self._upstream_headers(request),
                    timeout=_SSE_TIMEOUT if self._is_sse_request(request, path) else _TIMEOUT,
                ),
                stream=True,
            )
        except httpx.HTTPError as exc:
            # Previously only ConnectError/ConnectTimeout were caught, so a read
            # timeout or a malformed upstream reply escaped as an unhandled 500.
            log.warning("opencode request to %s failed: %r", target, exc)
            raise OpenCodeUnreachable(str(exc)) from exc

    # ------------------------------------------------------------------
    #  Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Connectivity probe.

        opencode exposes ``/global/health``; there is no bare ``/health``, so
        the previous probe reported the server as down 100% of the time.
        """
        try:
            r = await self._client.get(
                f"{self.base_url}/global/health",
                headers={"Authorization": self._auth_header},
                timeout=httpx.Timeout(5.0),
            )
            return {"ok": r.status_code == 200, "status": r.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
