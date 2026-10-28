"""studio_helpers.py — extracted helpers, models, and utilities for Studio."""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel

from core.database import StudioMedia, ModelEndpoint
from src.auth_helpers import _auth_disabled, require_user
from src.settings import get_user_setting, load_settings
from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)

def require_studio_privilege(request: Request) -> str:
    """Allow both browser sessions and API tokens, but enforce privilege."""
    if getattr(request.state, "api_token", False):
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        user = owner
    else:
        user = require_user(request)

    if not user:
        return user
        
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return user
        
    try:
        privs = auth_mgr.get_privileges(user) or {}
    except Exception:
        return user
        
    if not isinstance(privs, dict):
        privs = {}
        
    if not privs.get("can_generate_images", True):
        raise HTTPException(403, "Your account is not allowed to generate images.")
        
    return user


def _owner_filter(q, user, model_cls=StudioMedia):
    """Apply owner filtering to a studio query."""
    if user is not None:
        return q.filter(model_cls.owner == user)
    if _auth_disabled():
        return q
    return q.filter(False)

def _media_to_dict(media: StudioMedia) -> Dict[str, Any]:
    return {
        "id": media.id,
        "filename": media.filename,
        "url": f"/api/studio/media/{media.filename}",
        "media_type": media.media_type,
        "prompt": media.prompt,
        "model": media.model,
        "job_id": media.job_id,
        "job_status": media.job_status,
        "is_active": media.is_active,
        "favorite": media.favorite,
        "width": media.width,
        "height": media.height,
        "file_size": media.file_size,
        "duration": media.duration,
        "fps": media.fps,
        "source_media_id": media.source_media_id,
        "generation_mode": media.generation_mode,
        # Why a job failed — the client could previously only show "failed".
        "error": media.error,
        "seed": media.seed,
        "thumbnail_url": (
            f"/api/studio/thumbnail/{media.thumbnail}" if media.thumbnail else None
        ),
        "created_at": media.created_at.isoformat() if media.created_at else None,
        "updated_at": media.updated_at.isoformat() if media.updated_at else None,
    }

# ---------------------------------------------------------------------------
# Local image endpoints (Settings -> Models, model_type == "image")
# ---------------------------------------------------------------------------
# Studio model ids for these look like "local/<endpoint_id>/<model>", so a
# generate request names exactly one endpoint. Endpoint ids are short uuid
# prefixes (no slash); the model part may contain slashes.
LOCAL_MODEL_PREFIX = "local/"


def is_local_model_id(model_id: Optional[str]) -> bool:
    return bool(model_id) and model_id.startswith(LOCAL_MODEL_PREFIX)


def _visible_local_image_endpoints(db, user):
    from src.auth_helpers import owner_filter
    q = db.query(ModelEndpoint).filter(
        ModelEndpoint.model_type == "image",
        ModelEndpoint.is_enabled == True,  # noqa: E712
    )
    return owner_filter(q, ModelEndpoint, user).all()


def _endpoint_model_ids(ep) -> list:
    from routes.model_routes import _visible_models
    return _visible_models(ep.cached_models, ep.hidden_models, ep.pinned_models)


def local_api_base(ep, user) -> tuple:
    """``(base_url ending in /v1, auth headers)`` for a local image endpoint.

    The endpoint's key goes out as a bearer token on every call to it."""
    from src.endpoint_resolver import resolve_endpoint_runtime
    base, api_key = resolve_endpoint_runtime(ep, owner=user)
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base, ({"Authorization": f"Bearer {api_key}"} if api_key else {})


# Per-model capabilities from the endpoint's own GET /models
# (max_image_references, supports_seed). Briefly cached so opening the Studio
# doesn't wait on every endpoint each time; a failed lookup is cached shorter.
LOCAL_CAPS_TTL = 300.0
LOCAL_CAPS_ERROR_TTL = 60.0
_local_caps_cache: Dict[tuple, tuple] = {}


async def _local_model_capabilities(base: str, headers: dict) -> Dict[str, dict]:
    import hashlib
    import time
    import httpx

    key = (base, hashlib.sha256(repr(sorted(headers.items())).encode()).hexdigest())
    hit = _local_caps_cache.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base}/models", headers=headers)
        resp.raise_for_status()
        caps = {
            m["id"]: m for m in (resp.json().get("data") or [])
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        }
        ttl = LOCAL_CAPS_TTL
    except Exception as e:
        logger.warning("Local image endpoint %s: capability lookup failed: %s", base, e)
        caps, ttl = {}, LOCAL_CAPS_ERROR_TTL
    _local_caps_cache[key] = (time.monotonic() + ttl, caps)
    return caps


def _photo_entry(ep_id: str, model: str, caps: dict) -> Dict[str, Any]:
    # Unknown capabilities mean no references (the server would reject them)
    # but a seed, which every local diffusion server takes.
    max_refs = caps.get("max_image_references")
    if isinstance(max_refs, bool) or not isinstance(max_refs, int) or max_refs < 0:
        max_refs = 0
    seed = caps.get("supports_seed")
    return {
        "id": f"{LOCAL_MODEL_PREFIX}{ep_id}/{model}",
        "name": f"{model} (lokal)",
        "supports_character_reference": max_refs > 0,
        "max_image_references": max_refs,
        "supports_seed": seed if isinstance(seed, bool) else True,
        "pricing": {},
    }


def local_photo_targets(db, user) -> list:
    """``(endpoint_id, base, headers, model_ids)`` for every local image
    endpoint the user may use. Plain values, so the DB session can close
    before any network call."""
    targets = []
    for ep in _visible_local_image_endpoints(db, user):
        models = _endpoint_model_ids(ep)
        if models:
            base, headers = local_api_base(ep, user)
            targets.append((ep.id, base, headers, models))
    return targets


async def local_photo_models(targets: list) -> list:
    """Studio photo entries for *targets*, with each endpoint's capabilities."""
    import asyncio
    all_caps = await asyncio.gather(*(
        _local_model_capabilities(base, headers) for _, base, headers, _ in targets
    ))
    return [
        _photo_entry(ep_id, model, caps.get(model) or {})
        for (ep_id, _, _, models), caps in zip(targets, all_caps)
        for model in models
    ]


def resolve_local_photo_model(db, model_id: str, user):
    """Map a "local/<endpoint_id>/<model>" id to ``(endpoint, model)``.

    Raises 400 when the endpoint is not visible to *user* (or disabled, or not
    an image endpoint) or no longer offers the model.
    """
    _, _, rest = model_id.partition(LOCAL_MODEL_PREFIX)
    ep_id, _, model = rest.partition("/")
    if ep_id and model:
        for ep in _visible_local_image_endpoints(db, user):
            if ep.id == ep_id and model in _endpoint_model_ids(ep):
                return ep, model
    raise HTTPException(400, f"Local image model '{model_id}' is not available. Check Settings -> Models.")


def get_openrouter_api_key(db) -> Optional[str]:
    """Retrieve the OpenRouter API key from the database."""
    # Look for endpoint where kind is openrouter or base_url contains openrouter.ai
    from src.endpoint_resolver import resolve_endpoint_runtime
    
    # Try endpoints marked explicitly as openrouter
    ep = db.query(ModelEndpoint).filter(
        ModelEndpoint.is_enabled == True,
        ModelEndpoint.base_url.like("%openrouter.ai%")
    ).first()
    
    if not ep:
        return None
        
    # We use resolve_endpoint_runtime to decrypt the API key
    _, api_key = resolve_endpoint_runtime(ep)
    return api_key
