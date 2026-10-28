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


def local_photo_models(db, user) -> list:
    """Studio photo entries for every local image model the user may use."""
    entries = []
    for ep in _visible_local_image_endpoints(db, user):
        for model in _endpoint_model_ids(ep):
            entries.append({
                "id": f"{LOCAL_MODEL_PREFIX}{ep.id}/{model}",
                "name": f"{model} (lokal)",
                "supports_character_reference": False,
                "max_image_references": 0,
                "supports_seed": True,
                "pricing": {},
            })
    return entries


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
