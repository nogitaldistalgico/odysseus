import os
import asyncio
import uuid
import time
import httpx
import logging
import base64
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Request, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.database import SessionLocal, StudioMedia
from src.auth_helpers import get_current_user, effective_user
from src.constants import STUDIO_MEDIA_DIR, UPLOAD_DIR
from src.settings import load_settings, get_user_setting
from src.upload_limits import STUDIO_UPLOAD_MAX_BYTES, read_upload_limited
from routes.studio.studio_helpers import _owner_filter, _media_to_dict, get_openrouter_api_key, require_studio_privilege
from routes.studio.studio_preprocess import (
    get_model_constraints, preprocess_reference_image, validate_payload_params,
    supports_real_continuation, supports_video_editing,
)
from routes.studio.studio_ffmpeg import (
    get_video_info, extract_last_frame, concatenate_videos, is_ffmpeg_available,
)
import mimetypes

os.makedirs(STUDIO_MEDIA_DIR, exist_ok=True)
router = APIRouter()
logger = logging.getLogger(__name__)

async def _upload_video_temp(filepath: str) -> str:
    """Upload a local video file to tmpfiles.org and return the direct HTTPS download URL.
    This is required because some OpenRouter video models (like Runway Aleph 2) strictly 
    require a public HTTPS URL and reject base64 data URLs."""
    async with httpx.AsyncClient(timeout=120) as client:
        with open(filepath, "rb") as f:
            resp = await client.post("https://tmpfiles.org/api/v1/upload", files={"file": f})
        resp.raise_for_status()
        url = resp.json().get("data", {}).get("url")
        if not url:
            raise RuntimeError("Failed to upload video to temporary host")
        
        # Convert to direct download link
        return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")

STUDIO_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}

class PhotoGenRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    model: Optional[str] = None
    aspect_ratio: Optional[str] = "16:9"
    base_media_id: Optional[str] = None
    size: Optional[str] = None
    seed: Optional[int] = None
    steps: Optional[int] = None

class VideoGenRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    model: Optional[str] = None
    base_media_id: Optional[str] = None
    duration: Optional[int] = None
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = None
    size: Optional[str] = None
    generate_audio: Optional[bool] = None

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

class VideoEditRequest(BaseModel):
    source_video_id: str
    prompt: str
    model: Optional[str] = None
    aspect_ratio: Optional[str] = None

def _resolve_media_path(file_id: str) -> str:
    """Resolve a media file ID to its absolute path on disk."""
    path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.isfile(path):
        for root, dirs, files in os.walk(UPLOAD_DIR):
            if file_id in files:
                path = os.path.join(root, file_id)
                break
    if not os.path.isfile(path):
        raise HTTPException(404, "Base media file not found")
    return path


def _get_base64_data_url(file_id: str) -> str:
    """Read a media file and return it as a base64 data-URL (unchanged)."""
    path = _resolve_media_path(file_id)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        b64_str = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64_str}"


def _get_preprocessed_base64_data_url(file_id: str, model_constraints: dict) -> str:
    """Read a media file, preprocess it (resize/crop) for the target video
    model, and return the result as a base64 data-URL."""
    path = _resolve_media_path(file_id)
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

import time

_studio_models_cache = None
_studio_models_cache_time = 0
CACHE_TTL = 3600 # 1 hour

@router.get("/api/studio/models")
async def get_studio_models():
    """Returns a dynamic list of OpenRouter models for image and video generation.
    
    Video model entries include constraint metadata (supported_resolutions,
    supported_aspect_ratios, supported_sizes, supported_durations) so that
    clients can populate dropdowns with only valid values.
    """
    global _studio_models_cache, _studio_models_cache_time
    
    if _studio_models_cache and (time.time() - _studio_models_cache_time) < CACHE_TTL:
        return _studio_models_cache

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            img_resp, vid_resp, vid_constraints_resp = await asyncio.gather(
                client.get("https://openrouter.ai/api/v1/models?output_modalities=image"),
                client.get("https://openrouter.ai/api/v1/models?output_modalities=video"),
                client.get("https://openrouter.ai/api/v1/videos/models"),
            )
            
            photos = []
            if img_resp.status_code == 200:
                photos = [{"id": m["id"], "name": m.get("name", m["id"])} for m in img_resp.json().get("data", [])]

            # Build a lookup of per-model constraints from the dedicated
            # video-models endpoint (supported_sizes, resolutions, etc.).
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
                            "pricing_skus": m.get("pricing_skus", {}),
                            "description": m.get("description", ""),
                            "supported_parameters": m.get("supported_parameters", []),
                            "allowed_passthrough_parameters": m.get("allowed_passthrough_parameters", []),
                        }

            videos = []
            if vid_resp.status_code == 200:
                for m in vid_resp.json().get("data", []):
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
                    entry["supports_continuation"] = supports_real_continuation(c)
                    entry["supports_video_editing"] = supports_video_editing(c)
                    videos.append(entry)
                
            _studio_models_cache = {
                "photo": photos,
                "video": videos
            }
            _studio_models_cache_time = time.time()
            return _studio_models_cache
    except Exception as e:
        logger.error(f"Failed to fetch studio models from OpenRouter: {e}")
        # Fallback to a minimal list if the API call fails
        return _studio_models_cache or {
            "photo": [{"id": "google/gemini-3-pro-image", "name": "Google Nano Banana Pro (Gemini 3)"}],
            "video": [{"id": "google/veo-2.0-pro", "name": "Google Veo 2.0 Pro"}]
        }


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
    }

@router.get("/api/studio/library")
async def studio_library(
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
async def get_studio_media(request: Request, filename: str):
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

@router.delete("/api/studio/{media_id}")
async def delete_studio_media(request: Request, media_id: str):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.id == media_id).first()
        if not m or (m.owner and m.owner != user):
            raise HTTPException(404, "Media not found")
        m.is_active = False
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()

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
            "HTTP-Referer": "https://github.com/pewdiepie-archdaemon/odysseus",
            "X-OpenRouter-Title": "Odysseus Studio"
        }
        
        payload = {
            "model": target_model,
            "prompt": req.prompt
        }
        if req.negative_prompt:
            payload["negative_prompt"] = req.negative_prompt
        if req.size:
            payload["size"] = req.size
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.steps is not None:
            payload["steps"] = req.steps
        
        if req.base_media_id:
            refs = []
            for m_id in req.base_media_id.split(","):
                m_id = m_id.strip()
                if m_id:
                    refs.append({
                        "type": "image_url",
                        "image_url": {
                            "url": _get_base64_data_url(m_id)
                        }
                    })
            if refs:
                payload["input_references"] = refs

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
            filename = f"{media_id}.png"
            filepath = os.path.join(STUDIO_MEDIA_DIR, filename)
            
            if b64_data:
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(b64_data))
            else:
                img_resp = await client.get(url_data)
                img_resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(img_resp.content)

            new_media = StudioMedia(
                id=media_id,
                filename=filename,
                media_type="photo",
                prompt=req.prompt,
                model=target_model,
                owner=user,
                file_size=os.path.getsize(filepath)
            )
            db.add(new_media)
            db.commit()
            
            return _media_to_dict(new_media)
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
            "HTTP-Referer": "https://github.com/pewdiepie-archdaemon/odysseus",
            "X-OpenRouter-Title": "Odysseus Studio"
        }
        
        # Fetch model constraints for preprocessing and validation
        constraints = await get_model_constraints(target_model)

        payload = {
            "model": target_model,
            "prompt": req.prompt
        }
        if req.negative_prompt:
            payload["negative_prompt"] = req.negative_prompt
        if req.duration is not None:
            payload["duration"] = req.duration
        if req.resolution:
            payload["resolution"] = req.resolution
        if req.aspect_ratio:
            payload["aspect_ratio"] = req.aspect_ratio
        if req.size:
            payload["size"] = req.size
        if req.generate_audio is not None:
            payload["generate_audio"] = req.generate_audio
        
        if req.base_media_id:
            refs = []
            frame_imgs = []
            for idx, m_id in enumerate(req.base_media_id.split(",")):
                m_id = m_id.strip()
                if m_id:
                    # Preprocess: resize/crop the reference image to a
                    # resolution the target model accepts.
                    url = _get_preprocessed_base64_data_url(m_id, constraints)
                    refs.append({
                        "type": "image_url",
                        "image_url": {
                            "url": url
                        }
                    })
                    
                    # For video models on OpenRouter, the first image is typically the first_frame
                    if idx == 0:
                        frame_imgs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            },
                            "frame_type": "first_frame"
                        })
                    elif idx == 1:
                        # If a second image is passed, assume it's the last_frame
                        frame_imgs.append({
                            "type": "image_url",
                            "image_url": {
                                "url": url
                            },
                            "frame_type": "last_frame"
                        })

            if refs:
                payload["input_references"] = refs
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
            return _media_to_dict(new_media)
    except Exception as e:
        logger.exception("Video generation failed")
        raise HTTPException(500, str(e))
    finally:
        db.close()

@router.get("/api/studio/jobs/{media_id}")
async def check_video_job(request: Request, media_id: str):
    user = require_studio_privilege(request)
    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.id == media_id).first()
        if not m or (m.owner and m.owner != user):
            raise HTTPException(404, "Media not found")
        
        if m.job_status == "completed":
            return _media_to_dict(m)
            
        api_key = get_openrouter_api_key(db)
        headers = {"Authorization": f"Bearer {api_key}"}
        
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            poll_url = m.job_id
            if not poll_url.startswith("http"):
                if not poll_url.startswith("/"):
                    poll_url = f"/api/v1/generation?id={poll_url}"
                poll_url = f"https://openrouter.ai{poll_url}"
                
            resp = await client.get(poll_url, headers=headers)
            if resp.status_code not in (200, 202):
                raise HTTPException(500, f"Polling failed: {resp.status_code} {resp.text}")
                
            data = resp.json()
            status = data.get("status")
            
            if status == "completed":
                url_data = None
                
                # Check for new video API format (unsigned_urls)
                if "unsigned_urls" in data and data["unsigned_urls"]:
                    url_data = data["unsigned_urls"][0]
                # Fallback to image API format
                elif "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0 and "url" in data["data"][0]:
                    url_data = data["data"][0]["url"]
                
                if url_data:
                    filepath = os.path.join(STUDIO_MEDIA_DIR, m.filename)
                    req_headers = headers if "openrouter.ai" in url_data else None
                    vid_resp = await client.get(url_data, headers=req_headers)
                    vid_resp.raise_for_status()
                    with open(filepath, "wb") as f:
                        f.write(vid_resp.content)
                    
                    # Async concatenation
                    if m.source_media_id and str(m.source_media_id).endswith(":concat"):
                        real_source_id = str(m.source_media_id).split(":")[0]
                        source = db.query(StudioMedia).filter(StudioMedia.id == real_source_id).first()
                        if source:
                            source_path = os.path.join(STUDIO_MEDIA_DIR, source.filename)
                            if os.path.exists(source_path):
                                try:
                                    import uuid
                                    concat_id = f"stv_{uuid.uuid4().hex[:12]}"
                                    concat_path = os.path.join(STUDIO_MEDIA_DIR, f"{concat_id}.mp4")
                                    await concatenate_videos(source_path, filepath, concat_path)
                                    
                                    # Overwrite the segment with concatenated video
                                    os.replace(concat_path, filepath)
                                    logger.info("Concatenated async extended video for %s", m.id)
                                    
                                    if is_ffmpeg_available():
                                        c_info = await get_video_info(filepath)
                                        m.duration = c_info.get("duration")
                                        m.width = c_info.get("width")
                                        m.height = c_info.get("height")
                                        m.fps = c_info.get("fps")
                                except Exception as e:
                                    logger.warning("Async concatenation failed, keeping segment: %s", e)
                        m.source_media_id = real_source_id

                    m.file_size = os.path.getsize(filepath)
                    m.job_status = "completed"
                    db.commit()
                else:
                    m.job_status = "failed"
                    db.commit()
            elif status == "failed":
                m.job_status = "failed"
                db.commit()
                
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
            "HTTP-Referer": "https://github.com/pewdiepie-archdaemon/odysseus",
            "X-OpenRouter-Title": "Odysseus Studio",
        }

        # 3. Build payload
        payload: Dict[str, Any] = {
            "model": target_model,
            "prompt": req.prompt,
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
            # Upload source video to temporary host for OpenRouter HTTPS requirement
            video_url = await _upload_video_temp(source_path)

            payload["input_video"] = {
                "type": "video_url",
                "video_url": {"url": video_url},
            }
            payload["input_references"] = [
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                }
            ]
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
            "HTTP-Referer": "https://github.com/pewdiepie-archdaemon/odysseus",
            "X-OpenRouter-Title": "Odysseus Studio",
        }

        # 3. Build payload — upload source video to tmpfiles for OpenRouter HTTPS requirement
        video_url = await _upload_video_temp(source_path)

        payload: Dict[str, Any] = {
            "model": target_model,
            "prompt": req.prompt,
            "input_video": {
                "type": "video_url",
                "video_url": {"url": video_url},
            },
            "input_references": [
                {
                    "type": "video_url",
                    "video_url": {"url": video_url},
                }
            ],
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


@router.get("/api/studio/debug_video")
async def debug_video(request: Request):
    user = effective_user(request)
    db = SessionLocal()
    try:
        m = db.query(StudioMedia).filter(StudioMedia.media_type == "video").order_by(StudioMedia.created_at.desc()).first()
        if not m:
            return {"error": "No video found in database"}
            
        api_key = get_openrouter_api_key(db)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        
        poll_url = m.job_id or ""
        if poll_url and not poll_url.startswith("http"):
            if not poll_url.startswith("/"):
                poll_url = f"/api/v1/generation?id={poll_url}"
            poll_url = f"https://openrouter.ai{poll_url}"
            
        result = {
            "database_id": m.id,
            "filename": m.filename,
            "raw_job_id_in_db": m.job_id,
            "db_job_status": m.job_status,
            "computed_poll_url": poll_url
        }
        
        if not poll_url:
            return result
            
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(poll_url, headers=headers)
                result["openrouter_status_code"] = resp.status_code
                try:
                    result["openrouter_response"] = resp.json()
                except:
                    result["openrouter_response"] = resp.text
        except Exception as e:
            result["httpx_error"] = str(e)
            
        return result
    finally:
        db.close()
