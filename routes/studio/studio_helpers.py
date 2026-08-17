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
        "created_at": media.created_at.isoformat() if media.created_at else None,
        "updated_at": media.updated_at.isoformat() if media.updated_at else None,
    }

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
