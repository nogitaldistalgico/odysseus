# Companion bridge

A thin, additive layer so a LAN client (e.g. a phone) can discover what an
Odysseus server offers and pair to it, without duplicating any LLM logic.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/companion/ping` | session or token | cheap, auth-validated health check |
| GET | `/api/companion/info` | session or token | server identity + capability flags |
| GET | `/api/companion/models` | session or token | the **caller's own** model endpoints |
| GET | `/api/companion/pair` | **admin cookie** | pairing page (a form; never mints) |
| POST | `/api/companion/pair` | **admin cookie** | mint a one-time pairing token (`?format=json` for an in-app screen) |

`/info` lists `capabilities.widgets`: a client that can render ```` ```widget ````
fences (a self-contained HTML fragment, run sandboxed; see
`static/js/widgetDocument.js`) sends `widgets=true` (form field or JSON) with
`/api/chat_stream` or `/api/chat`, and the model is told it may answer with one.
Widgets are plain markdown in the message content, so history sync carries
them unchanged; a client without a renderer simply shows the code block.

`/models` scopes to the caller's real owner plus legacy null-owner shared rows
(same rule as `owner_filter`) and never returns API-key material.

## Pairing CSRF posture

Minting happens **only on POST**. The session cookie is `SameSite=Lax`
(`routes/auth_routes.py`), so a browser will not send it on a cross-site POST —
the same protection `POST /api/tokens` relies on. A `GET` would be unsafe (Lax
cookies ride top-level GET navigations), so `GET /pair` only renders a form.
Minting invalidates the auth middleware's token cache, so a freshly minted token
works on the next request without a restart.

The pairing/scoping rules live in small, tested units (`token_owner`,
`owner_can_see`, `mint_pairing_token`, `pairing.*`) — see
`tests/test_companion_readonly.py` and `tests/test_companion_pairing.py`.
