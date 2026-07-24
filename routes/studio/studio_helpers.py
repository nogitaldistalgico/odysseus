"""studio_helpers.py — extracted helpers, models, and utilities for Studio."""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel

from core.database import StudioMedia, ModelEndpoint
from src.auth_helpers import _auth_disabled
from src.settings import get_user_setting, load_settings

logger = logging.getLogger(__name__)

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
