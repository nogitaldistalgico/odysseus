import os
import asyncio
import uuid
import time
from datetime import datetime, timedelta
import httpx
import logging
import base64
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Request, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.database import SessionLocal, StudioMedia, StudioCharacter
from src.auth_helpers import get_current_user, effective_user
from src.constants import STUDIO_MEDIA_DIR, STUDIO_CHARACTERS_DIR, STUDIO_THUMBNAIL_DIR, UPLOAD_DIR
from src.settings import load_settings, get_user_setting
from src.upload_limits import STUDIO_UPLOAD_MAX_BYTES, read_upload_limited
from routes.studio.studio_helpers import _owner_filter, _media_to_dict, get_openrouter_api_key, require_studio_privilege
from routes.studio.studio_preprocess import (
    get_model_constraints, preprocess_reference_image, validate_payload_params,
    supports_real_continuation, supports_video_editing, supports_character_reference,
)
from routes.studio.studio_ffmpeg import (
    get_video_info, extract_last_frame, extract_frame_at, concatenate_videos, is_ffmpeg_available,
)
from src.s3_utils import upload_video_and_get_presigned_url
import mimetypes

os.makedirs(STUDIO_MEDIA_DIR, exist_ok=True)
os.makedirs(STUDIO_THUMBNAIL_DIR, exist_ok=True)
router = APIRouter()
logger = logging.getLogger(__name__)

STUDIO_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}

# Sent to OpenRouter for attribution. Was hardcoded to the upstream project;
# override with ODYSSEUS_STUDIO_REFERER if you want your own.
STUDIO_REFERER = os.getenv("ODYSSEUS_STUDIO_REFERER", "https://github.com/odysseus-dev/odysseus")

# Model used to expand a short prompt into a detailed one. claude-3.5-sonnet was
# hardcoded here; keep it configurable so it doesn't rot again.
STUDIO_MAGIC_PROMPT_MODEL = os.getenv(
    "ODYSSEUS_STUDIO_MAGIC_PROMPT_MODEL", "anthropic/claude-sonnet-4.5"
)

import enum

class MediaReferenceRole(str, enum.Enum):
    REFERENCE = "reference"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"

class MediaReference(BaseModel):
    id: str
    role: MediaReferenceRole = MediaReferenceRole.REFERENCE

class PhotoGenRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    model: Optional[str] = None
    aspect_ratio: Optional[str] = "16:9"
    base_media_id: Optional[str] = None
    media_references: Optional[List[MediaReference]] = None
    character_ids: Optional[List[str]] = None
    character_prompt_suffix: Optional[str] = None
    character_mapping_template: Optional[str] = None
    size: Optional[str] = None
    seed: Optional[int] = None
    steps: Optional[int] = None

class VideoGenRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    model: Optional[str] = None
    base_media_id: Optional[str] = None
    media_references: Optional[List[MediaReference]] = None
    character_ids: Optional[List[str]] = None
    character_prompt_suffix: Optional[str] = None
    character_mapping_template: Optional[str] = None
    duration: Optional[int] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    size: Optional[str] = None
    generate_audio: Optional[bool] = None
    upload_method: str = "s3"

class VideoExtendRequest(BaseModel):
    source_video_id: str
    prompt: str
    model: Optional[str] = None
    duration: Optional[int] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    use_real_continuation: bool = False
    concatenate: bool = True
    generate_audio: Optional[bool] = None
    media_references: Optional[List[MediaReference]] = None
    character_ids: Optional[List[str]] = None
    character_prompt_suffix: Optional[str] = None
    character_mapping_template: Optional[str] = None
    upload_method: str = "s3"

class VideoEditRequest(BaseModel):
    source_video_id: str
    prompt: str
    model: Optional[str] = None
    aspect_ratio: Optional[str] = None
    media_references: Optional[List[MediaReference]] = None
    character_ids: Optional[List[str]] = None
    character_prompt_suffix: Optional[str] = None
    character_mapping_template: Optional[str] = None
    upload_method: str = "s3"

class MagicPromptRequest(BaseModel):
    prompt: str
    media_id: Optional[str] = None
    model: Optional[str] = None  # falls back to STUDIO_MAGIC_PROMPT_MODEL
    system_prompt: Optional[str] = None

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def _image_extension(data: bytes, content_type: Optional[str], url: Optional[str]) -> str:
    """Pick a file extension that matches the bytes we actually received.

    Magic numbers first (authoritative), then the Content-Type header, then the
    URL, then PNG as the historical default.
    """
    for magic, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    if url:
        url_ext = os.path.splitext(url.split("?")[0])[1].lower()
        if url_ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            return url_ext
    return ".png"


def _path_inside(root: str, path: str) -> bool:
    """True when *path* really resolves inside *root* (symlinks included)."""
    try:
        root_real = os.path.realpath(root)
        return os.path.commonpath([root_real, os.path.realpath(path)]) == root_real
    except Exception:
        return False


def _resolve_media_path(file_id: str, owner: Optional[str] = None) -> str:
    """Resolve a media reference to an absolute path on disk.

    ``file_id`` is attacker-controlled: it arrives straight from
    ``media_references[].id`` / ``base_media_id`` in the request body. Joining
    it onto UPLOAD_DIR unchecked let "../../../etc/passwd" resolve outside the
    data directory, and the caller then base64-encodes that file into an
    OpenRouter payload (or uploads it to S3 and hands back a presigned URL).
    So: the id must be a bare filename, and every candidate path is verified to
    resolve inside a permitted root.

    Studio-generated media is resolvable too (by media id or filename), which
    is what lets a library item be used as a reference for the next
    generation. That lookup is owner-scoped when *owner* is given.
    """
    # A reference is a single filesystem name — never a path.
    if not file_id or file_id in (".", "..") or os.path.basename(file_id) != file_id:
        raise HTTPException(400, "Invalid media reference")

    # 1) Studio media, resolved through the DB so ownership is enforced.
    db = SessionLocal()
    try:
        q = db.query(StudioMedia).filter(
            (StudioMedia.id == file_id) | (StudioMedia.filename == file_id)
        )
        m = q.first()
        if m is not None:
            if owner is not None and m.owner and m.owner != owner:
                raise HTTPException(404, "Base media file not found")
            candidate = os.path.join(STUDIO_MEDIA_DIR, m.filename)
            if os.path.isfile(candidate) and _path_inside(STUDIO_MEDIA_DIR, candidate):
                return candidate
    finally:
        db.close()

    # 2) Uploads. Mirrors _resolve_upload_path in routes/upload_routes.py.
    direct = os.path.join(UPLOAD_DIR, file_id)
    if os.path.isfile(direct) and _path_inside(UPLOAD_DIR, direct):
        return direct

    for root, _dirs, files in os.walk(UPLOAD_DIR, followlinks=False):
        if file_id in files:
            path = os.path.join(root, file_id)
            if os.path.isfile(path) and _path_inside(UPLOAD_DIR, path):
                return path

    raise HTTPException(404, "Base media file not found")


def _get_base64_data_url(file_id: str, owner: Optional[str] = None) -> str:
    """Read a media file and return it as a base64 data-URL (unchanged)."""
    path = _resolve_media_path(file_id, owner)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        b64_str = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64_str}"


def _get_preprocessed_base64_data_url(file_id: str, model_constraints: dict, owner: Optional[str] = None) -> str:
    """Read a media file, preprocess it (resize/crop) for the target video
    model, and return the result as a base64 data-URL."""
    path = _resolve_media_path(file_id, owner)
    with open(path, "rb") as f:
        raw_bytes = f.read()

    processed = preprocess_reference_image(raw_bytes, model_constraints)

    # After preprocessing the output is always PNG
    if processed is not raw_bytes:
        mime = "image/png"
    else:
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

    b64_str = base64.b64encode(processed).decode("utf-8")
    return f"data:{mime};base64,{b64_str}"

async def _get_s3_url(file_id: str, expiration: int = 300, owner: Optional[str] = None) -> str:
    from src.s3_utils import upload_video_and_get_presigned_url
    path = _resolve_media_path(file_id, owner)
    object_name = f"studio_refs/{file_id}"
    return await upload_video_and_get_presigned_url(path, object_name, expiration)

async def _get_preprocessed_s3_url(file_id: str, model_constraints: dict, expiration: int = 300, owner: Optional[str] = None) -> str:
    import tempfile
    from src.s3_utils import upload_video_and_get_presigned_url
    path = _resolve_media_path(file_id, owner)
    with open(path, "rb") as f:
        raw_bytes = f.read()

    processed = preprocess_reference_image(raw_bytes, model_constraints)
    
    ext = ".png" if processed is not raw_bytes else os.path.splitext(path)[1]
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(processed)
        tmp_path = tmp.name

    object_name = f"studio_refs/{file_id}_preprocessed{ext}"
    try:
        url = await upload_video_and_get_presigned_url(tmp_path, object_name, expiration)
    finally:
        os.remove(tmp_path)
    return url

_studio_models_cache = None
_studio_models_cache_time = 0
CACHE_TTL = 3600  # 1 hour
# When OpenRouter is down, every request used to re-issue four upstream calls
# with a 15s timeout before falling back. Remember the failure briefly and
# serialise the refresh so one outage can't turn into a thundering herd.
_studio_models_error_time = 0.0
STUDIO_MODELS_ERROR_TTL = 60.0
_studio_models_lock = asyncio.Lock()

@router.get("/api/studio/models")
async def get_studio_models():
    """Returns a dynamic list of OpenRouter models for image and video generation.
    
    Video model entries include constraint metadata (supported_resolutions,
    supported_aspect_ratios, supported_sizes, supported_durations) so that
    clients can populate dropdowns with only valid values.
    
    Both photo and video entries include ``supports_character_reference``
    derived from each model's ``architecture.input_modalities``.
    """
    global _studio_models_cache, _studio_models_cache_time, _studio_models_error_time

    if _studio_models_cache and (time.time() - _studio_models_cache_time) < CACHE_TTL:
        return _studio_models_cache

    async with _studio_models_lock:
        # Another request may have refreshed while we waited for the lock.
        if _studio_models_cache and (time.time() - _studio_models_cache_time) < CACHE_TTL:
            return _studio_models_cache
        if (time.time() - _studio_models_error_time) < STUDIO_MODELS_ERROR_TTL:
            return _studio_models_cache or _STUDIO_MODELS_FALLBACK
        return await _fetch_studio_models()


_STUDIO_MODELS_FALLBACK = {
    "photo": [{"id": "google/gemini-3-pro-image", "name": "Google Nano Banana Pro (Gemini 3)",
               "supports_character_reference": True}],
    "video": [{"id": "google/veo-3.1", "name": "Google Veo 3.1",
               "supports_character_reference": True}],
}


async def _fetch_studio_models():
    global _studio_models_cache, _studio_models_cache_time, _studio_models_error_time
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            img_resp, vid_resp, vid_constraints_resp, img_constraints_resp = await asyncio.gather(
                client.get("https://openrouter.ai/api/v1/models?output_modalities=image"),
                client.get("https://openrouter.ai/api/v1/models?output_modalities=video"),
                client.get("https://openrouter.ai/api/v1/videos/models"),
                client.get("https://openrouter.ai/api/v1/images/models"),
            )
            
            if img_resp.status_code != 200 or vid_resp.status_code != 200:
                raise Exception(f"OpenRouter returned non-200 status: img={img_resp.status_code}, vid={vid_resp.status_code}")

            # Parse Image Constraints
            img_constraints_by_id: Dict[str, Any] = {}
            if img_constraints_resp.status_code == 200:
                for m in img_constraints_resp.json().get("data", []):
                    mid = m.get("id")
                    if mid:
                        img_constraints_by_id[mid] = m.get("supported_parameters", {})

            # ----------------------------------------------------------
            # Photo models — parse exact capabilities
            # ----------------------------------------------------------
            photos = []
            for m in img_resp.json().get("data", []):
                mid = m["id"]
                c = img_constraints_by_id.get(mid, {})
                
                # Default to fallback heuristic if exact constraints not found
                arch = m.get("architecture", {})
                input_mods = arch.get("input_modalities") or []
                fallback_supports_image = "image" in input_mods
                
                # Check for input_references in supported_parameters
                input_refs = c.get("input_references", {})
                max_refs = input_refs.get("max")
                
                if max_refs is not None:
                    supports_character = max_refs > 0
                else:
                    supports_character = fallback_supports_image
                    max_refs = 1 if fallback_supports_image else 0
                
                entry = {
                    "id": mid,
                    "name": m.get("name", mid),
                    "supports_character_reference": supports_character,
                    "max_image_references": max_refs,
                    "supports_seed": "seed" in c,
                    "pricing": m.get("pricing", {}),
                }
                photos.append(entry)

            # ----------------------------------------------------------
            # Video constraints from the dedicated /videos/models endpoint
            # ----------------------------------------------------------
            constraints_by_id: Dict[str, Any] = {}
            if vid_constraints_resp.status_code == 200:
                for m in vid_constraints_resp.json().get("data", []):
                    mid = m.get("id")
                    if mid:
                        constraints_by_id[mid] = {
                            "supported_resolutions": m.get("supported_resolutions", []),
                            "supported_aspect_ratios": m.get("supported_aspect_ratios", []),
                            "supported_sizes": m.get("supported_sizes", []),
                            "supported_durations": m.get("supported_durations", []),
                            "supported_frame_images": m.get("supported_frame_images"),
                            "generate_audio": m.get("generate_audio", False),
                            "seed": m.get("seed", False),
                            "pricing_skus": m.get("pricing_skus", {}),
                            "description": m.get("description", ""),
                            "supported_parameters": m.get("supported_parameters", []),
                            "allowed_passthrough_parameters": m.get("allowed_passthrough_parameters", []),
                        }

            # ----------------------------------------------------------
            # Video models
            # ----------------------------------------------------------
            videos = []
            for m in vid_resp.json().get("data", []):
                arch = m.get("architecture", {})
                input_mods = arch.get("input_modalities") or []
                
                entry: Dict[str, Any] = {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                }
                # Merge constraints if available
                c = constraints_by_id.get(m["id"], {})
                entry["supported_resolutions"] = c.get("supported_resolutions", [])
                entry["supported_aspect_ratios"] = c.get("supported_aspect_ratios", [])
                entry["supported_sizes"] = c.get("supported_sizes", [])
                entry["supported_durations"] = c.get("supported_durations", [])
                
                if "supported_frame_images" in c:
                    entry["supported_frame_images"] = c["supported_frame_images"]
                    
                entry["supports_audio"] = c.get("generate_audio")
                entry["supports_seed"] = c.get("seed")
                
                entry["supports_continuation"] = supports_real_continuation(c)
                entry["supports_video_editing"] = supports_video_editing(c)
                entry["pricing"] = c.get("pricing_skus", {})
                
                # ------------------------------------------------------
                # Capability flags are TRI-STATE: True / False / None.
                # None means "OpenRouter tells us nothing", which is not the
                # same as "unsupported" — a client must not filter a model out
                # on None. Two separate capabilities live here and used to be
                # conflated:
                #
                #   supports_frame_images     — first/last frame conditioning
                #   supports_character_reference — the character library
                #
                # gating a reference-image UI on the character flag hid frame
                # conditioning on models that support it.
                # ------------------------------------------------------
                frame_images = c.get("supported_frame_images")
                entry["supports_frame_images"] = (
                    None if frame_images is None else bool(frame_images)
                )

                supported_params = c.get("supported_parameters") or []
                if isinstance(supported_params, dict):
                    supported_params = list(supported_params.keys())
                param_names = {str(x).lower() for x in supported_params}
                allowed_passthrough = {
                    str(x).lower() for x in (c.get("allowed_passthrough_parameters") or [])
                }
                ref_params = {"input_references", "reference_images", "references", "images"}

                supports_refs: Optional[bool] = None
                max_refs: Optional[int] = None

                raw_refs = c.get("input_references")
                if isinstance(raw_refs, dict) and raw_refs.get("max") is not None:
                    max_refs = int(raw_refs["max"])
                    supports_refs = max_refs > 0
                elif ref_params & (param_names | allowed_passthrough):
                    supports_refs = True
                else:
                    # No structured signal exists for this in OpenRouter's video
                    # catalogue today, so the description is all there is. A hit
                    # is meaningful; a miss is NOT evidence of absence, so it
                    # stays None rather than becoming a false negative — with
                    # `False` most of the catalogue would be wrongly excluded.
                    desc = str(c.get("description", "")).lower()
                    if any(kw in desc for kw in [
                        "reference-to-video", "reference-based", "reference images",
                        "reference-conditioned", "character consistency",
                    ]):
                        supports_refs = True

                entry["supports_character_reference"] = supports_refs
                # Only a number we actually know; not derived from the flag.
                entry["max_image_references"] = max_refs
                
                videos.append(entry)
                
            _studio_models_cache = {
                "photo": photos,
                "video": videos
            }
            _studio_models_cache_time = time.time()
            return _studio_models_cache
    except Exception as e:
        logger.error(f"Failed to fetch studio models from OpenRouter: {e}")
        _studio_models_error_time = time.time()
        # Serve the last good list when we have one; otherwise a minimal stub.
        return _studio_models_cache or _STUDIO_MODELS_FALLBACK


@router.get("/api/studio/model-constraints/{model_id:path}")
async def get_model_constraints_endpoint(model_id: str):
    """Return the generation constraints for a specific video model.
    
    The iOS client should call this when the user selects a video model,
    to populate dropdowns for resolution, aspect ratio, duration, etc.
    with only the values that the chosen model actually supports.
    
    Response example:
    ```json
    {
        "model_id": "google/veo-3.1",
        "supported_resolutions": ["720p", "1080p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_sizes": ["1280x720", "720x1280", "1920x1080", "1080x1920", "1080x1080"],
        "supported_durations": [5, 8]
    }
    ```
    """
    constraints = await get_model_constraints(model_id)
    return {
        "model_id": model_id,
        "supported_resolutions": constraints.get("supported_resolutions", []),
        "supported_aspect_ratios": constraints.get("supported_aspect_ratios", []),
        "supported_sizes": constraints.get("supported_sizes", []),
        "supported_durations": constraints.get("supported_durations", []),
        "supported_frame_images": constraints.get("supported_frame_images"),
        "supports_continuation": supports_real_continuation(constraints),
        "supports_video_editing": supports_video_editing(constraints),
        "supports_character_reference": supports_character_reference(constraints),
    }

class CostEstimateRequest(BaseModel):
    model: str
    duration: Optional[float] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    generate_audio: Optional[bool] = None
    reference_image_count: int = 0
    is_continuation: bool = False


@router.post("/api/studio/estimate-cost")
async def estimate_cost(request: Request, req: CostEstimateRequest):
    """What one generation with these parameters will cost, in USD.

    The pricing data already travels with /api/studio/models but was only ever
    passed through; this turns it into a number the client can show before the
    user commits to a run. Token-priced models return usd=null with a reason
    rather than a guessed figure.
    """
    require_studio_privilege(request)
    from routes.studio.studio_pricing import estimate_video_cost

    constraints = await get_model_constraints(req.model)
    skus = constraints.get("pricing_skus") or {}

    duration = req.duration
    if duration is None:
        supported = constraints.get("supported_durations") or []
        duration = supported[0] if supported else None

    result = estimate_video_cost(
        skus,
        duration=duration,
        resolution=req.resolution,
        generate_audio=req.generate_audio,
        reference_image_count=req.reference_image_count,
        is_continuation=req.is_continuation,
    )
    result["model"] = req.model
    result["duration"] = duration
    result["currency"] = "USD"
    return result


@router.get("/api/studio/library")
def studio_library(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
) -> Dict[str, Any]:
    user = effective_user(request)
    db = SessionLocal()
    try:
        try:
            q = db.query(StudioMedia).filter(StudioMedia.is_active == True)
            q = _owner_filter(q, user)
            total = q.count()
            rows = q.order_by(StudioMedia.created_at.desc()).offset(offset).limit(limit).all()
        except Exception as e:
            logger.error(f"Error executing DB query in studio_library: {repr(e)}", exc_info=True)
            raise
        try:
            return {
                "media": [_media_to_dict(m) for m in rows],
                "total": total,
                "offset": offset,
                "limit": limit
            }
        except Exception as e:
            logger.error(f"Error dictifying media in studio_library: {repr(e)}", exc_info=True)
            raise
    finally:
        db.close()

@router.get("/api/studio/media/{filename}")
def get_studio_media(request: Request, filename: str):
    user = effective_user(request)
    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.filename == filename).first()
        if not m or (m.owner and m.owner != user):
            raise HTTPException(404, "Media not found")
        
        path = os.path.join(STUDIO_MEDIA_DIR, filename)
        if not os.path.exists(path):
            raise HTTPException(404, "File not found on disk")
        return FileResponse(path)
    finally:
        db.close()

async def _apply_character_references(prompt: str, character_ids: List[str], db, refs: List[Dict], suffix: Optional[str] = None, mapping_template: Optional[str] = None, use_s3: bool = False, owner: Optional[str] = None) -> str:
    """Replaces character names in the prompt with pseudonyms and appends their images to refs.

    The character lookup is owner-scoped: without it any studio user could pull
    another user's characters — and therefore their reference face images —
    into their own generation payload just by passing the id.
    """
    import re
    import string
    
    if not character_ids:
        return prompt
        
    q = db.query(StudioCharacter).filter(StudioCharacter.id.in_(character_ids))
    if owner is not None:
        q = q.filter(StudioCharacter.owner_id == owner)
    chars = q.all()

    # Whether the caller had already staged a scene reference before we append
    # character images. This used to be read off the *last* character's
    # start_idx after the loop, so with two or more characters the scene suffix
    # was appended even when no scene image existed.
    had_scene_reference = len(refs) > 0

    meta_instructions = []
    
    for i, char in enumerate(chars):
        pseudo = f"[Person {string.ascii_uppercase[i]}]"
        
        # Replace name with pseudo (case-insensitive) using word boundaries
        pattern = re.compile(rf'\b{re.escape(char.name)}\b', re.IGNORECASE)
        prompt = pattern.sub(pseudo, prompt)
        
        # Load images
        import json
        images = json.loads(char.images_json) if char.images_json else []
        start_idx = len(refs) + 1
        
        for img_filename in images:
            filepath = os.path.join(STUDIO_CHARACTERS_DIR, char.id, img_filename)
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    raw_bytes = f.read()
                
                # Downscale character images to prevent massive JSON payloads that cause timeouts
                processed_bytes = preprocess_reference_image(raw_bytes, {})
                
                if use_s3:
                    import tempfile
                    from src.s3_utils import upload_video_and_get_presigned_url
                    ext = ".png" if processed_bytes is not raw_bytes else os.path.splitext(filepath)[1]
                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                        tmp.write(processed_bytes)
                        tmp_path = tmp.name
                    object_name = f"studio_chars/{char.id}_{img_filename}_preprocessed{ext}"
                    try:
                        url = await upload_video_and_get_presigned_url(tmp_path, object_name, expiration=300)
                    finally:
                        os.remove(tmp_path)
                else:
                    b64_data = base64.b64encode(processed_bytes).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(filepath)
                    if not mime_type:
                        mime_type = "image/png"
                    url = f"data:{mime_type};base64,{b64_data}"
                    
                refs.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url
                    }
                })
        end_idx = len(refs)
        
        if start_idx <= end_idx:
            if mapping_template:
                try:
                    meta = mapping_template.format(pseudo=pseudo, start_idx=start_idx, end_idx=end_idx)
                except Exception:
                    # Fallback to default if the template format fails
                    meta = f"Character {pseudo} is depicted in reference images {start_idx} to {end_idx}. Ensure exact facial consistency."
            else:
                meta = f"Character {pseudo} is depicted in reference images {start_idx} to {end_idx}. Ensure exact facial consistency."
            meta_instructions.append(meta)
            
    if meta_instructions:
        prompt += "\n\n" + " ".join(meta_instructions)
        # If there's an initial reference image (idx 1), it's the scene.
        if had_scene_reference:
            if suffix:
                prompt += " " + suffix
            else:
                prompt += " The first reference image dictates the overall scene composition and style."
            
    return prompt

@router.get("/api/studio/thumbnail/{filename}")
def get_studio_thumbnail(request: Request, filename: str):
    """Serve a video poster frame.

    Ownership is resolved through the owning media row, the same way
    /api/studio/media/{filename} does it.
    """
    user = effective_user(request)
    if os.path.basename(filename) != filename or filename in (".", ".."):
        raise HTTPException(404, "Thumbnail not found")

    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.thumbnail == filename).first()
        if not m or (m.owner and m.owner != user):
            raise HTTPException(404, "Thumbnail not found")
    finally:
        db.close()

    path = os.path.join(STUDIO_THUMBNAIL_DIR, filename)
    if not os.path.isfile(path) or not _path_inside(STUDIO_THUMBNAIL_DIR, path):
        raise HTTPException(404, "Thumbnail not found")
    # Poster frames are immutable once written.
    return FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})


class MediaPatchRequest(BaseModel):
    favorite: Optional[bool] = None
    prompt: Optional[str] = None


def _lookup_owned_media(db, media_id: str, user: Optional[str]):
    """Resolve a library item by id *or* filename, enforcing ownership.

    GET /api/studio/media/{filename} keys on the filename while clients
    naturally hold the id, so both are accepted rather than making callers
    guess which one a given verb wants.
    """
    m = (
        db.query(StudioMedia)
        .filter((StudioMedia.id == media_id) | (StudioMedia.filename == media_id))
        .first()
    )
    if not m or (m.owner and m.owner != user):
        raise HTTPException(404, "Media not found")
    return m


async def _update_studio_media(request: Request, media_id: str, req: MediaPatchRequest):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        m = _lookup_owned_media(db, media_id, user)
        if req.favorite is not None:
            m.favorite = req.favorite
        if req.prompt is not None:
            m.prompt = req.prompt[:8000]
        db.commit()
        return _media_to_dict(m)
    finally:
        db.close()


@router.patch("/api/studio/media/{media_id}")
async def update_studio_media(request: Request, media_id: str, req: MediaPatchRequest):
    """Update mutable fields on a library item.

    `favorite` existed on the model and was returned by the API, but there was
    no way to set it — the flag was effectively dead.
    """
    return await _update_studio_media(request, media_id, req)


@router.patch("/api/studio/{media_id}", include_in_schema=False)
async def update_studio_media_legacy(request: Request, media_id: str, req: MediaPatchRequest):
    """Deprecated alias. Prefer PATCH /api/studio/media/{media_id}."""
    return await _update_studio_media(request, media_id, req)


async def _delete_studio_media(request: Request, media_id: str):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        m = _lookup_owned_media(db, media_id, user)
        m.is_active = False
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.delete("/api/studio/media/{media_id}")
async def delete_studio_media(request: Request, media_id: str):
    """Soft-delete a library item (the file is kept on disk)."""
    return await _delete_studio_media(request, media_id)


@router.delete("/api/studio/{media_id}", include_in_schema=False)
async def delete_studio_media_legacy(request: Request, media_id: str):
    """Deprecated alias.

    A single-segment catch-all directly under /api/studio sits alongside the
    named collections (/models, /library, /characters, /upload, ...), so a
    future DELETE /api/studio/<collection> would land here as
    media_id="<collection>". Prefer DELETE /api/studio/media/{media_id}.
    """
    return await _delete_studio_media(request, media_id)

@router.post("/api/studio/generate/photo")
async def generate_photo(request: Request, req: PhotoGenRequest):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        api_key = get_openrouter_api_key(db)
        if not api_key:
            raise HTTPException(400, "OpenRouter API key not configured. Add OpenRouter in Settings -> Models.")
        
        settings = load_settings()
        target_model = req.model or get_user_setting("studio_openrouter_photo_model", user, settings.get("studio_openrouter_photo_model", ""))
        if not target_model:
            raise HTTPException(400, "No photo model specified. Set a default in Settings -> Studio or pass a model.")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": STUDIO_REFERER,
            "X-OpenRouter-Title": "Odysseus Studio"
        }
        
        payload = {
            "model": target_model,
        }
        
        refs = []
        # Reference images are downscaled before they go out. The video path
        # already did this; photos sent the original, so a 12 MP phone photo
        # became ~16 MB of base64 in the request body. An empty constraints
        # dict makes find_best_size fall back to its 1024 px cap.
        _photo_constraints: dict = {}

        # Backward compatibility for base_media_id string
        if req.base_media_id:
            for m_id in req.base_media_id.split(","):
                m_id = m_id.strip()
                if m_id:
                    refs.append({
                        "type": "image_url",
                        "image_url": {
                            "url": _get_preprocessed_base64_data_url(m_id, _photo_constraints, user)
                        }
                    })
                    
        # New structured references
        if req.media_references:
            for m_ref in req.media_references:
                if m_ref.id:
                    # For photos, all references are treated as input_references regardless of role
                    refs.append({
                        "type": "image_url",
                        "image_url": {
                            "url": _get_preprocessed_base64_data_url(m_ref.id, _photo_constraints, user)
                        }
                    })

        prompt = req.prompt
        if req.character_ids:
            prompt = await _apply_character_references(
                prompt, req.character_ids, db, refs, 
                req.character_prompt_suffix, req.character_mapping_template,
                owner=user,
            )
            
        payload["prompt"] = prompt
        
        if refs:
            payload["input_references"] = refs

        if req.negative_prompt:
            payload["negative_prompt"] = req.negative_prompt
        if req.size:
            payload["size"] = req.size
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.steps is not None:
            payload["steps"] = req.steps

        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            resp = await client.post("https://openrouter.ai/api/v1/images", json=payload, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(500, f"OpenRouter API error: {resp.status_code} {resp.text}")
            
            data = resp.json()
            b64_data = data.get("data", [{}])[0].get("b64_json")
            url_data = data.get("data", [{}])[0].get("url")
            
            if not b64_data and not url_data:
                raise HTTPException(500, "No image data returned from OpenRouter.")
            
            media_id = f"st_{uuid.uuid4().hex[:12]}"

            # The extension used to be hardcoded to .png even when the model
            # answered with a JPEG or WebP URL, so the stored file's type and
            # its name disagreed and clients mis-rendered it.
            if b64_data:
                image_bytes = base64.b64decode(b64_data)
                content_type = None
            else:
                img_resp = await client.get(url_data)
                img_resp.raise_for_status()
                image_bytes = img_resp.content
                content_type = img_resp.headers.get("content-type")

            ext = _image_extension(image_bytes, content_type, url_data)
            filename = f"{media_id}{ext}"
            filepath = os.path.join(STUDIO_MEDIA_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)

            new_media = StudioMedia(
                id=media_id,
                filename=filename,
                media_type="photo",
                prompt=req.prompt,
                model=target_model,
                owner=user,
                seed=req.seed,
                generation_mode="generate",
                file_size=os.path.getsize(filepath)
            )
            db.add(new_media)
            db.commit()
            
            return _media_to_dict(new_media)
    except HTTPException:
        # HTTPException is an Exception, so without this the deliberate 400s
        # above ("OpenRouter API key not configured", "No photo model
        # specified") were re-raised as 500s and the client could no longer
        # tell a misconfiguration from a server fault.
        raise
    except Exception as e:
        logger.exception("Photo generation failed")
        raise HTTPException(500, str(e))
    finally:
        db.close()

@router.post("/api/studio/generate/video")
async def generate_video(request: Request, req: VideoGenRequest):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        api_key = get_openrouter_api_key(db)
        if not api_key:
            raise HTTPException(400, "OpenRouter API key not configured. Add OpenRouter in Settings -> Models.")
        
        settings = load_settings()
        target_model = req.model or get_user_setting("studio_openrouter_video_model", user, settings.get("studio_openrouter_video_model", ""))
        if not target_model:
            raise HTTPException(400, "No video model specified. Set a default in Settings -> Studio or pass a model.")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": STUDIO_REFERER,
            "X-OpenRouter-Title": "Odysseus Studio"
        }
        
        # Fetch model constraints for preprocessing and validation
        constraints = await get_model_constraints(target_model)

        payload = {
            "model": target_model,
        }
        
        refs = []
        frame_imgs = []
        
        # Backward compatibility: base_media_id forces idx=0 to first_frame and idx=1 to last_frame
        if req.base_media_id:
            for idx, m_id in enumerate(req.base_media_id.split(",")):
                m_id = m_id.strip()
                if m_id:
                    url = _get_preprocessed_base64_data_url(m_id, constraints, user)
                    # For legacy base_media_id, we always put it in refs as well
                    refs.append({
                        "type": "image_url",
                        "image_url": {
                            "url": url
                        }
                    })
                    if idx == 0:
                        frame_imgs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            },
                            "frame_type": "first_frame"
                        })
                    elif idx == 1:
                        frame_imgs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            },
                            "frame_type": "last_frame"
                        })
                        
        # New structured references: Gives full control to the client over roles
        if req.media_references:
            for m_ref in req.media_references:
                if m_ref.id:
                    if m_ref.role == MediaReferenceRole.FIRST_FRAME:
                        url = _get_preprocessed_base64_data_url(m_ref.id, constraints, user)
                        frame_imgs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            },
                            "frame_type": "first_frame"
                        })
                    elif m_ref.role == MediaReferenceRole.LAST_FRAME:
                        url = _get_preprocessed_base64_data_url(m_ref.id, constraints, user)
                        frame_imgs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            },
                            "frame_type": "last_frame"
                        })
                    else:
                        # Default is REFERENCE, which goes into input_references via S3
                        if req.upload_method == "s3":
                            url = await _get_preprocessed_s3_url(m_ref.id, constraints, expiration=300, owner=user)
                        else:
                            url = _get_preprocessed_base64_data_url(m_ref.id, constraints, user)
                        refs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            }
                        })


        prompt = req.prompt
        if req.character_ids:
            # We pass refs by reference, so characters are appended to refs, but NOT to frame_imgs
            prompt = await _apply_character_references(
                prompt, req.character_ids, db, refs, 
                req.character_prompt_suffix, req.character_mapping_template,
                use_s3=(req.upload_method == "s3"), owner=user,
            )
            
        payload["prompt"] = prompt

        if refs:
            payload["input_references"] = refs
        if frame_imgs:
            payload["frame_images"] = frame_imgs

        # Validate / correct resolution, aspect_ratio, duration against
        # what the target model actually supports.
        payload = validate_payload_params(payload, constraints)

        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            resp = await client.post("https://openrouter.ai/api/v1/videos", json=payload, headers=headers)
            # OpenRouter typically returns job/polling info with 202 Accepted
            if resp.status_code not in (200, 202):
                raise HTTPException(500, f"OpenRouter API error: {resp.status_code} {resp.text}")
            
            data = resp.json()
            # If it returns synchronous result (unlikely but possible)
            url_data = data.get("data", [{}])[0].get("url")
            polling_url = data.get("polling_url")
            job_id = data.get("id")

            media_id = f"stv_{uuid.uuid4().hex[:12]}"
            filename = f"{media_id}.mp4"
            
            new_media = StudioMedia(
                id=media_id,
                filename=filename,
                media_type="video",
                prompt=req.prompt,
                model=target_model,
                owner=user,
                job_id=polling_url or job_id, # store polling url as job id for simplicity
                job_status="pending" if polling_url else "completed"
            )
            
            if url_data:
                # Sync completion
                filepath = os.path.join(STUDIO_MEDIA_DIR, filename)
                req_headers = headers if "openrouter.ai" in url_data else None
                vid_resp = await client.get(url_data, headers=req_headers)
                vid_resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(vid_resp.content)
                new_media.file_size = os.path.getsize(filepath)

            db.add(new_media)
            db.commit()

            if new_media.job_status == "completed":
                thumb = await _generate_thumbnail(new_media)
                if thumb:
                    new_media.thumbnail = thumb
                    db.commit()

            return _media_to_dict(new_media)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Video generation failed")
        raise HTTPException(500, str(e))
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Video job finalisation
#
# A generated video only becomes a file on disk once somebody polls OpenRouter
# and downloads the result. That used to happen exclusively inside the client's
# poll request, so a job whose client went away — app backgrounded, killed,
# network lost — stayed "pending" forever and the paid generation was lost.
# The logic lives here so both the poll endpoint and the background finaliser
# (see _studio_job_finaliser_loop in app.py) can drive it.
# ---------------------------------------------------------------------------

# One in-flight finalisation per media id: the request path and the background
# loop can otherwise download and concatenate the same job twice.
_job_locks: Dict[str, asyncio.Lock] = {}


def _job_lock(media_id: str) -> asyncio.Lock:
    lock = _job_locks.get(media_id)
    if lock is None:
        # Keep the map from growing for the lifetime of the process. Only
        # unlocked entries are dropped, so nothing waiting is disturbed.
        if len(_job_locks) > 512:
            for key in [k for k, v in _job_locks.items() if not v.locked()]:
                _job_locks.pop(key, None)
        lock = asyncio.Lock()
        _job_locks[media_id] = lock
    return lock


def _poll_url_for(job_id: Optional[str]) -> Optional[str]:
    """Build the OpenRouter polling URL from a stored job id.

    Returns None when there is nothing to poll — job_id could be NULL, which
    previously reached `.startswith` and raised AttributeError inside the
    request.
    """
    if not job_id:
        return None
    poll_url = str(job_id)
    if not poll_url.startswith("http"):
        if not poll_url.startswith("/"):
            poll_url = f"/api/v1/generation?id={poll_url}"
        poll_url = f"https://openrouter.ai{poll_url}"
    return poll_url


async def _generate_thumbnail(media: StudioMedia) -> Optional[str]:
    """Write a poster frame for a video and return its filename.

    The library previously handed out only the full-size asset, so scrolling it
    on a phone pulled entire videos just to draw tiles.
    """
    if media.media_type != "video" or not is_ffmpeg_available():
        return None
    src = os.path.join(STUDIO_MEDIA_DIR, media.filename)
    if not os.path.isfile(src):
        return None
    try:
        info = await get_video_info(src)
        # A frame slightly into the clip is more representative than frame 0,
        # which is often black.
        at = min(1.0, max(0.0, (info.get("duration") or 2.0) * 0.25))
        frame = await extract_frame_at(src, at)
        thumb_name = f"{os.path.splitext(media.filename)[0]}_thumb.jpg"
        thumb_path = os.path.join(STUDIO_THUMBNAIL_DIR, thumb_name)
        os.makedirs(STUDIO_THUMBNAIL_DIR, exist_ok=True)

        from routes.studio.studio_preprocess import _ensure_pil
        if _ensure_pil():
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(frame)).convert("RGB")
            img.thumbnail((640, 640), Image.LANCZOS)
            img.save(thumb_path, format="JPEG", quality=82)
        else:
            thumb_name = f"{os.path.splitext(media.filename)[0]}_thumb.png"
            thumb_path = os.path.join(STUDIO_THUMBNAIL_DIR, thumb_name)
            with open(thumb_path, "wb") as f:
                f.write(frame)
        return thumb_name
    except Exception as e:
        logger.warning("Thumbnail generation failed for %s: %s", media.id, e)
        return None


async def finalize_video_job(media_id: str) -> bool:
    """Poll one pending video job and, when it is done, persist the result.

    Returns True when the job reached a terminal state (completed or failed).
    Safe to call concurrently for the same id and safe to call repeatedly.
    """
    async with _job_lock(media_id):
        db = SessionLocal()
        try:
            m = db.query(StudioMedia).filter(StudioMedia.id == media_id).first()
            if not m:
                return True
            if m.job_status in ("completed", "failed"):
                return True

            poll_url = _poll_url_for(m.job_id)
            if not poll_url:
                m.job_status = "failed"
                m.error = "No polling URL was returned for this generation."
                db.commit()
                return True

            api_key = get_openrouter_api_key(db)
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                resp = await client.get(poll_url, headers=headers)
                if resp.status_code not in (200, 202):
                    # Transient upstream trouble: leave the job pending so the
                    # next tick retries rather than burning the generation.
                    logger.warning(
                        "Polling %s failed: %s %s", media_id, resp.status_code, resp.text[:300]
                    )
                    return False

                data = resp.json()
                status = data.get("status")

                if status == "failed":
                    m.job_status = "failed"
                    m.error = str(
                        data.get("error") or data.get("message") or "The provider reported a failure."
                    )[:2000]
                    db.commit()
                    return True

                if status != "completed":
                    return False

                url_data = None
                if "unsigned_urls" in data and data["unsigned_urls"]:
                    url_data = data["unsigned_urls"][0]
                elif (
                    "data" in data and isinstance(data["data"], list)
                    and len(data["data"]) > 0 and "url" in data["data"][0]
                ):
                    url_data = data["data"][0]["url"]

                if not url_data:
                    m.job_status = "failed"
                    m.error = "The provider reported success but returned no video URL."
                    db.commit()
                    return True

                filepath = os.path.join(STUDIO_MEDIA_DIR, m.filename)
                req_headers = headers if "openrouter.ai" in url_data else None
                vid_resp = await client.get(url_data, headers=req_headers)
                vid_resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(vid_resp.content)

            # Deferred concatenation for extend jobs.
            if m.source_media_id and str(m.source_media_id).endswith(":concat"):
                real_source_id = str(m.source_media_id).split(":")[0]
                source = db.query(StudioMedia).filter(StudioMedia.id == real_source_id).first()
                if source:
                    source_path = os.path.join(STUDIO_MEDIA_DIR, source.filename)
                    if os.path.exists(source_path):
                        try:
                            concat_path = os.path.join(
                                STUDIO_MEDIA_DIR, f"stv_{uuid.uuid4().hex[:12]}.mp4"
                            )
                            await concatenate_videos(source_path, filepath, concat_path)
                            os.replace(concat_path, filepath)
                            logger.info("Concatenated extended video for %s", m.id)
                        except Exception as e:
                            logger.warning("Concatenation failed, keeping segment: %s", e)
                            m.error = f"Extension generated, but joining it to the source failed: {e}"
                m.source_media_id = real_source_id

            if is_ffmpeg_available():
                info = await get_video_info(filepath)
                m.duration = info.get("duration") or m.duration
                m.width = info.get("width") or m.width
                m.height = info.get("height") or m.height
                m.fps = info.get("fps") or m.fps

            m.file_size = os.path.getsize(filepath)
            m.job_status = "completed"
            db.commit()

            thumb = await _generate_thumbnail(m)
            if thumb:
                m.thumbnail = thumb
                db.commit()

            return True
        except Exception as e:
            logger.exception("Finalising video job %s failed", media_id)
            try:
                m = db.query(StudioMedia).filter(StudioMedia.id == media_id).first()
                if m and m.job_status == "pending":
                    m.error = str(e)[:2000]
                    db.commit()
            except Exception:
                pass
            return False
        finally:
            db.close()


async def finalize_pending_video_jobs(max_age_hours: int = 48) -> int:
    """Finalise every still-pending video job. Driven by the background loop."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        rows = (
            db.query(StudioMedia.id)
            .filter(StudioMedia.job_status == "pending")
            .filter(StudioMedia.created_at >= cutoff)
            .all()
        )
        ids = [r[0] for r in rows]
    finally:
        db.close()

    done = 0
    for media_id in ids:
        try:
            if await finalize_video_job(media_id):
                done += 1
        except Exception:
            logger.debug("Finaliser skipped %s", media_id, exc_info=True)
    return done


@router.get("/api/studio/jobs/{media_id}")
async def check_video_job(request: Request, media_id: str):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.id == media_id).first()
        if not m or (m.owner and m.owner != user):
            raise HTTPException(404, "Media not found")
        if m.job_status in ("completed", "failed"):
            return _media_to_dict(m)
    finally:
        db.close()

    # Whoever gets there first finalises; the lock keeps the background loop
    # and this request from doing the work twice.
    await finalize_video_job(media_id)

    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.id == media_id).first()
        if not m:
            raise HTTPException(404, "Media not found")
        return _media_to_dict(m)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Video Upload
# ---------------------------------------------------------------------------

@router.post("/api/studio/upload")
async def studio_upload_video(request: Request, file: UploadFile = File(...)):
    """Upload a video file to the Studio library.

    Accepts mp4, mov, webm, mkv, m4v up to 100 MB (configurable via
    ODYSSEUS_STUDIO_UPLOAD_MAX_BYTES).  Video metadata (duration, resolution,
    fps) is extracted via FFmpeg/ffprobe when available.
    """
    user = require_studio_privilege(request)

    # Validate file extension
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in STUDIO_VIDEO_EXTS:
        raise HTTPException(
            400,
            f"Unsupported file type '.{ext}'. Accepted: {', '.join(sorted(STUDIO_VIDEO_EXTS))}",
        )

    data = await read_upload_limited(file, STUDIO_UPLOAD_MAX_BYTES, "Studio video upload")

    media_id = f"stu_{uuid.uuid4().hex[:12]}"
    filename = f"{media_id}.{ext}"
    filepath = os.path.join(STUDIO_MEDIA_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(data)

    # Extract video metadata via FFmpeg (best-effort)
    info = {}
    if is_ffmpeg_available():
        try:
            info = await get_video_info(filepath)
        except Exception as e:
            logger.warning("Failed to read video metadata for %s: %s", filename, e)

    db = SessionLocal()
    try:
        new_media = StudioMedia(
            id=media_id,
            filename=filename,
            media_type="video",
            prompt="",
            owner=user,
            job_status="completed",
            file_size=len(data),
            width=info.get("width"),
            height=info.get("height"),
            duration=info.get("duration"),
            fps=info.get("fps"),
            generation_mode="upload",
        )
        db.add(new_media)
        db.commit()

        thumb = await _generate_thumbnail(new_media)
        if thumb:
            new_media.thumbnail = thumb
            db.commit()

        return _media_to_dict(new_media)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Video Extension (Continuation)
# ---------------------------------------------------------------------------

@router.post("/api/studio/extend-video")
async def extend_video(request: Request, req: VideoExtendRequest):
    """Extend an existing video by generating a new segment and optionally
    concatenating it to the original.

    Supports two modes:
    - ``use_real_continuation=false`` (default): extracts the last frame of the
      source video and sends it as ``first_frame`` to the model.  Works with
      any model that supports ``first_frame``.  Cheaper.
    - ``use_real_continuation=true``: sends the entire source video as reference
      input so the model can continue the motion.  Only available for models
      whose ``pricing_skus`` contain ``video_input`` or ``video_continuation``.
      More expensive but produces seamless results.
    """
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        # 1. Load source video
        source = db.query(StudioMedia).filter(
            StudioMedia.id == req.source_video_id,
            StudioMedia.is_active == True,
        ).first()
        if not source or (source.owner and source.owner != user):
            raise HTTPException(404, "Source video not found")
        if source.media_type != "video":
            raise HTTPException(400, "Source media is not a video")
        if source.job_status != "completed":
            raise HTTPException(400, "Source video is not yet ready (still generating)")

        source_path = os.path.join(STUDIO_MEDIA_DIR, source.filename)
        if not os.path.isfile(source_path):
            raise HTTPException(404, "Source video file not found on disk")

        # 2. Resolve model & constraints
        api_key = get_openrouter_api_key(db)
        if not api_key:
            raise HTTPException(400, "OpenRouter API key not configured.")

        settings = load_settings()
        target_model = req.model or get_user_setting(
            "studio_openrouter_video_model", user,
            settings.get("studio_openrouter_video_model", ""),
        )
        if not target_model:
            raise HTTPException(400, "No video model specified.")

        constraints = await get_model_constraints(target_model)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": STUDIO_REFERER,
            "X-OpenRouter-Title": "Odysseus Studio",
        }

        refs = []
        if req.media_references:
            for m_ref in req.media_references:
                # In extend mode, we only respect "reference" role since the video drives the timeline
                if m_ref.id and m_ref.role == MediaReferenceRole.REFERENCE:
                    url = await _get_preprocessed_s3_url(m_ref.id, constraints, expiration=300, owner=user)
                    refs.append({
                        "type": "image_url",
                        "image_url": {"url": url}
                    })

        prompt = req.prompt
        if req.character_ids:
            prompt = await _apply_character_references(
                prompt, req.character_ids, db, refs,
                req.character_prompt_suffix, req.character_mapping_template,
                use_s3=(req.upload_method == "s3"), owner=user,
            )

        # 3. Build payload
        payload: Dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
        }
        if req.duration is not None:
            payload["duration"] = req.duration
        if req.resolution:
            payload["resolution"] = req.resolution
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.generate_audio is not None:
            payload["generate_audio"] = req.generate_audio

        generation_mode: str

        if req.use_real_continuation:
            # --- Real continuation: send entire video as reference ---
            if not supports_real_continuation(constraints):
                raise HTTPException(
                    400,
                    f"Model '{target_model}' does not support real video continuation. "
                    "Set use_real_continuation=false to use frame-based extension instead.",
                )

            # Read source video and encode as base64 data URL
            # Upload source video to user's private S3 bucket and generate a presigned HTTPS URL for OpenRouter
            s3_object_name = f"studio_export_{os.path.basename(source_path)}"
            video_url = await upload_video_and_get_presigned_url(source_path, s3_object_name)

            payload["input_video"] = {
                "type": "video_url",
                "video_url": {"url": video_url},
            }
            # OpenRouter allows multiple references, including video + images
            payload["input_references"] = [
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                }
            ] + refs
            generation_mode = "extend_continuation"
            logger.info("Video extend: using real continuation for model %s", target_model)
        else:
            # --- Frame-based fallback: extract last frame ---
            if not is_ffmpeg_available():
                raise HTTPException(
                    500,
                    "FFmpeg is not installed on the server. Cannot extract video frames.",
                )

            last_frame_png = await extract_last_frame(source_path)
            frame_b64 = base64.b64encode(last_frame_png).decode("utf-8")
            frame_data_url = f"data:image/png;base64,{frame_b64}"

            # Preprocess the frame for the target model
            preprocessed = preprocess_reference_image(last_frame_png, constraints)
            if preprocessed is not last_frame_png:
                frame_b64 = base64.b64encode(preprocessed).decode("utf-8")
                frame_data_url = f"data:image/png;base64,{frame_b64}"

            payload["frame_images"] = [{
                "type": "image_url",
                "image_url": {"url": frame_data_url},
                "frame_type": "first_frame",
            }]
            generation_mode = "extend_frame"
            logger.info("Video extend: using last-frame fallback for model %s", target_model)
            if refs:
                payload["input_references"] = refs

        # Validate payload params
        payload = validate_payload_params(payload, constraints)

        # 4. Send to OpenRouter
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/videos",
                json=payload, headers=headers,
            )
            if resp.status_code not in (200, 202):
                raise HTTPException(500, f"OpenRouter API error: {resp.status_code} {resp.text}")

            data = resp.json()
            url_data = data.get("data", [{}])[0].get("url")
            polling_url = data.get("polling_url")
            job_id = data.get("id")

            media_id = f"stv_{uuid.uuid4().hex[:12]}"
            filename = f"{media_id}.mp4"

            new_media = StudioMedia(
                id=media_id,
                filename=filename,
                media_type="video",
                prompt=req.prompt,
                model=target_model,
                owner=user,
                job_id=polling_url or job_id,
                job_status="pending" if polling_url else "completed",
                source_media_id=f"{req.source_video_id}:concat" if req.concatenate else req.source_video_id,
                generation_mode=generation_mode,
            )

            if url_data:
                filepath = os.path.join(STUDIO_MEDIA_DIR, filename)
                req_headers = headers if "openrouter.ai" in url_data else None
                vid_resp = await client.get(url_data, headers=req_headers)
                vid_resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(vid_resp.content)
                new_media.file_size = os.path.getsize(filepath)

                # Concatenate if requested and sync-completed
                if req.concatenate:
                    try:
                        concat_id = f"stv_{uuid.uuid4().hex[:12]}"
                        concat_filename = f"{concat_id}.mp4"
                        concat_path = os.path.join(STUDIO_MEDIA_DIR, concat_filename)
                        await concatenate_videos(source_path, filepath, concat_path)

                        concat_info = await get_video_info(concat_path) if is_ffmpeg_available() else {}
                        new_media.id = concat_id
                        new_media.filename = concat_filename
                        new_media.file_size = os.path.getsize(concat_path)
                        new_media.duration = concat_info.get("duration")
                        new_media.width = concat_info.get("width")
                        new_media.height = concat_info.get("height")
                        new_media.fps = concat_info.get("fps")
                        # Clean up the un-concatenated segment
                        os.remove(filepath)
                        logger.info("Concatenated extended video: %s", concat_filename)
                    except Exception as e:
                        logger.warning("Concatenation failed, keeping segment only: %s", e)

            db.add(new_media)
            db.commit()

            result = _media_to_dict(new_media)
            result["concatenated"] = req.concatenate
            result["continuation_mode"] = generation_mode
            return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Video extension failed")
        raise HTTPException(500, str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Video Editing
# ---------------------------------------------------------------------------

@router.post("/api/studio/edit-video")
async def edit_video(request: Request, req: VideoEditRequest):
    """Edit an existing video using AI (e.g. Runway Aleph 2.0).

    Sends the source video and a text prompt describing the desired changes
    to a video-editing model.  The original video is preserved; the edited
    result is stored as a new StudioMedia entry.
    """
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        # 1. Load source video
        source = db.query(StudioMedia).filter(
            StudioMedia.id == req.source_video_id,
            StudioMedia.is_active == True,
        ).first()
        if not source or (source.owner and source.owner != user):
            raise HTTPException(404, "Source video not found")
        if source.media_type != "video":
            raise HTTPException(400, "Source media is not a video")
        if source.job_status != "completed":
            raise HTTPException(400, "Source video is not yet ready (still generating)")

        source_path = os.path.join(STUDIO_MEDIA_DIR, source.filename)
        if not os.path.isfile(source_path):
            raise HTTPException(404, "Source video file not found on disk")

        # 2. Resolve model & constraints
        api_key = get_openrouter_api_key(db)
        if not api_key:
            raise HTTPException(400, "OpenRouter API key not configured.")

        settings = load_settings()
        target_model = req.model or get_user_setting(
            "studio_openrouter_video_model", user,
            settings.get("studio_openrouter_video_model", ""),
        )
        if not target_model:
            raise HTTPException(400, "No video model specified.")

        constraints = await get_model_constraints(target_model)

        if not supports_video_editing(constraints):
            raise HTTPException(
                400,
                f"Model '{target_model}' does not support video editing. "
                "Use a video editing model like 'runway/aleph-2'.",
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": STUDIO_REFERER,
            "X-OpenRouter-Title": "Odysseus Studio",
        }

        refs = []
        if req.media_references:
            for m_ref in req.media_references:
                if m_ref.id and m_ref.role == MediaReferenceRole.REFERENCE:
                    url = await _get_preprocessed_s3_url(m_ref.id, constraints, expiration=300, owner=user)
                    refs.append({
                        "type": "image_url",
                        "image_url": {"url": url}
                    })

        prompt = req.prompt
        if req.character_ids:
            prompt = await _apply_character_references(
                prompt, req.character_ids, db, refs,
                req.character_prompt_suffix, req.character_mapping_template,
                use_s3=(req.upload_method == "s3"), owner=user,
            )

        # 3. Build payload — upload source video to S3 and generate Presigned URL
        s3_object_name = f"studio_export_{os.path.basename(source_path)}"
        video_url = await upload_video_and_get_presigned_url(source_path, s3_object_name)

        payload: Dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
            "input_video": {
                "type": "video_url",
                "video_url": {"url": video_url},
            },
            "input_references": [
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                }
            ] + refs,
        }
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio

        payload = validate_payload_params(payload, constraints)

        # 4. Send to OpenRouter
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/videos",
                json=payload, headers=headers,
            )
            if resp.status_code not in (200, 202):
                raise HTTPException(500, f"OpenRouter API error: {resp.status_code} {resp.text}")

            data = resp.json()
            url_data = data.get("data", [{}])[0].get("url")
            polling_url = data.get("polling_url")
            job_id = data.get("id")

            media_id = f"stv_{uuid.uuid4().hex[:12]}"
            filename = f"{media_id}.mp4"

            new_media = StudioMedia(
                id=media_id,
                filename=filename,
                media_type="video",
                prompt=req.prompt,
                model=target_model,
                owner=user,
                job_id=polling_url or job_id,
                job_status="pending" if polling_url else "completed",
                source_media_id=req.source_video_id,
                generation_mode="edit",
            )

            if url_data:
                filepath = os.path.join(STUDIO_MEDIA_DIR, filename)
                req_headers = headers if "openrouter.ai" in url_data else None
                vid_resp = await client.get(url_data, headers=req_headers)
                vid_resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(vid_resp.content)
                new_media.file_size = os.path.getsize(filepath)

                if is_ffmpeg_available():
                    try:
                        info = await get_video_info(filepath)
                        new_media.duration = info.get("duration")
                        new_media.width = info.get("width")
                        new_media.height = info.get("height")
                        new_media.fps = info.get("fps")
                    except Exception:
                        pass

            db.add(new_media)
            db.commit()
            return _media_to_dict(new_media)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Video editing failed")
        raise HTTPException(500, str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Studio Characters (Consistent Characters)
# ---------------------------------------------------------------------------

class CreateCharacterRequest(BaseModel):
    name: str

@router.post("/api/studio/characters")
async def create_character(req: CreateCharacterRequest, request: Request):
    user_id = require_studio_privilege(request)
    char_id = f"char_{uuid.uuid4().hex[:12]}"
    
    char_dir = os.path.join(STUDIO_CHARACTERS_DIR, char_id)
    os.makedirs(char_dir, exist_ok=True)
    
    db = SessionLocal()
    try:
        new_char = StudioCharacter(
            id=char_id,
            owner_id=user_id,
            name=req.name,
            images_json="[]"
        )
        db.add(new_char)
        db.commit()
        db.refresh(new_char)
        return {
            "id": new_char.id,
            "name": new_char.name,
            "images": []
        }
    finally:
        db.close()

@router.get("/api/studio/characters")
def get_characters(request: Request):
    user_id = require_studio_privilege(request)
    db = SessionLocal()
    try:
        chars = db.query(StudioCharacter).filter(StudioCharacter.owner_id == user_id).all()
        result = []
        for c in chars:
            import json
            images = json.loads(c.images_json) if c.images_json else []
            result.append({
                "id": c.id,
                "name": c.name,
                "images": images
            })
        return result
    finally:
        db.close()

@router.post("/api/studio/characters/{char_id}/images")
async def upload_character_image(char_id: str, request: Request, file: UploadFile = File(...)):
    user_id = require_studio_privilege(request)
    db = SessionLocal()
    try:
        char = db.query(StudioCharacter).filter(
            StudioCharacter.id == char_id,
            StudioCharacter.owner_id == user_id
        ).first()
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
            
        ext = file.filename.split('.')[-1].lower() if file.filename else "jpg"
        if ext not in ["jpg", "jpeg", "png", "webp"]:
            raise HTTPException(status_code=400, detail="Invalid image extension")
            
        filename = f"{uuid.uuid4().hex[:8]}.{ext}"
        char_dir = os.path.join(STUDIO_CHARACTERS_DIR, char_id)
        os.makedirs(char_dir, exist_ok=True)
        filepath = os.path.join(char_dir, filename)
        
        file_bytes = await read_upload_limited(file, 10_000_000) # 10MB limit
        with open(filepath, "wb") as f:
            f.write(file_bytes)
            
        import json
        images = json.loads(char.images_json) if char.images_json else []
        images.append(filename)
        char.images_json = json.dumps(images)
        db.commit()
        
        return {"filename": filename}
    finally:
        db.close()

@router.delete("/api/studio/characters/{char_id}/images/{filename}")
async def delete_character_image(char_id: str, filename: str, request: Request):
    user_id = require_studio_privilege(request)
    db = SessionLocal()
    try:
        char = db.query(StudioCharacter).filter(
            StudioCharacter.id == char_id,
            StudioCharacter.owner_id == user_id
        ).first()
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
            
        import json
        images = json.loads(char.images_json) if char.images_json else []
        if filename in images:
            images.remove(filename)
            char.images_json = json.dumps(images)
            db.commit()
            
            filepath = os.path.join(STUDIO_CHARACTERS_DIR, char_id, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                
        return {"status": "ok"}
    finally:
        db.close()

@router.delete("/api/studio/characters/{char_id}")
async def delete_character(char_id: str, request: Request):
    user_id = require_studio_privilege(request)
    db = SessionLocal()
    try:
        char = db.query(StudioCharacter).filter(
            StudioCharacter.id == char_id,
            StudioCharacter.owner_id == user_id
        ).first()
        if not char:
            raise HTTPException(status_code=404, detail="Character not found")
            
        db.delete(char)
        db.commit()
        
        import shutil
        char_dir = os.path.join(STUDIO_CHARACTERS_DIR, char_id)
        if os.path.exists(char_dir):
            shutil.rmtree(char_dir)
            
        return {"status": "ok"}
    finally:
        db.close()

@router.get("/api/studio/characters/{char_id}/images/{filename}")
def get_character_image(char_id: str, filename: str, request: Request):
    """Serve a character image directly.

    This route only checked the *privilege*, not ownership — every other
    character route filters on owner_id, so anyone who knew a char_id could
    read another user's reference faces. Both path segments are also joined
    onto a directory, so they are constrained to bare names and the result is
    verified to stay inside STUDIO_CHARACTERS_DIR.
    """
    user_id = require_studio_privilege(request)

    if os.path.basename(char_id) != char_id or os.path.basename(filename) != filename \
            or char_id in (".", "..") or filename in (".", ".."):
        raise HTTPException(status_code=404, detail="Image not found")

    db = SessionLocal()
    try:
        char = db.query(StudioCharacter).filter(
            StudioCharacter.id == char_id,
            StudioCharacter.owner_id == user_id,
        ).first()
        if not char:
            raise HTTPException(status_code=404, detail="Image not found")

        import json
        images = json.loads(char.images_json) if char.images_json else []
        if filename not in images:
            raise HTTPException(status_code=404, detail="Image not found")
    finally:
        db.close()

    filepath = os.path.join(STUDIO_CHARACTERS_DIR, char_id, filename)
    if not os.path.isfile(filepath) or not _path_inside(STUDIO_CHARACTERS_DIR, filepath):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(filepath)


@router.post("/api/studio/magic-prompt")
async def generate_magic_prompt(request: Request, req: MagicPromptRequest):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        api_key = get_openrouter_api_key(db)
        if not api_key:
            raise HTTPException(400, "OpenRouter API key not configured. Add OpenRouter in Settings -> Models.")
            
        sys_prompt = req.system_prompt or "You are an expert prompt engineer for AI video and image generators. Enhance the user's short prompt into a highly detailed, descriptive prompt suitable for Midjourney or Sora. Output ONLY the enhanced prompt, nothing else."
        
        messages = [
            {"role": "system", "content": sys_prompt}
        ]
        
        user_content = [{"type": "text", "text": req.prompt}]
        
        if req.media_id:
            path = _resolve_media_path(req.media_id, user)
            mime_type, _ = mimetypes.guess_type(path)
            if mime_type and mime_type.startswith("video"):
                if is_ffmpeg_available():
                    try:
                        frame_bytes = await extract_last_frame(path)
                        b64_str = base64.b64encode(frame_bytes).decode('utf-8')
                        user_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64_str}"}
                        })
                    except Exception as e:
                        logger.warning(f"Failed to extract frame for magic prompt: {e}")
            else:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": _get_base64_data_url(req.media_id, user)}
                })
                
        messages.append({"role": "user", "content": user_content})
        
        payload = {
            "model": req.model or STUDIO_MAGIC_PROMPT_MODEL,
            "messages": messages,
            "max_tokens": 1000
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": STUDIO_REFERER,
            "X-OpenRouter-Title": "Odysseus Studio"
        }
        
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(500, f"OpenRouter API error: {resp.text}")
                
            data = resp.json()
            enhanced = data["choices"][0]["message"]["content"].strip()
            
            return {"enhanced_prompt": enhanced}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Magic prompt failed")
        raise HTTPException(500, str(e))
    finally:
        db.close()
