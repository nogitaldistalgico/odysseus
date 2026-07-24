import os
import asyncio
import uuid
import time
import httpx
import logging
import base64
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.database import SessionLocal, StudioMedia
from src.auth_helpers import get_current_user, require_privilege, effective_user
from src.constants import STUDIO_MEDIA_DIR, UPLOAD_DIR
from src.settings import load_settings, get_user_setting
from routes.studio.studio_helpers import _owner_filter, _media_to_dict, get_openrouter_api_key
import mimetypes

os.makedirs(STUDIO_MEDIA_DIR, exist_ok=True)
router = APIRouter()
logger = logging.getLogger(__name__)

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

def _get_base64_data_url(file_id: str) -> str:
    path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.isfile(path):
        for root, dirs, files in os.walk(UPLOAD_DIR):
            if file_id in files:
                path = os.path.join(root, file_id)
                break
    if not os.path.isfile(path):
        raise HTTPException(404, "Base media file not found")
        
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        b64_str = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64_str}"

import time

_studio_models_cache = None
_studio_models_cache_time = 0
CACHE_TTL = 3600 # 1 hour

@router.get("/api/studio/models")
async def get_studio_models():
    """Returns a dynamic list of OpenRouter models for image and video generation."""
    global _studio_models_cache, _studio_models_cache_time
    
    if _studio_models_cache and (time.time() - _studio_models_cache_time) < CACHE_TTL:
        return _studio_models_cache

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            img_resp, vid_resp = await asyncio.gather(
                client.get("https://openrouter.ai/api/v1/models?output_modalities=image"),
                client.get("https://openrouter.ai/api/v1/models?output_modalities=video")
            )
            
            photos = []
            if img_resp.status_code == 200:
                photos = [{"id": m["id"], "name": m.get("name", m["id"])} for m in img_resp.json().get("data", [])]
                
            videos = []
            if vid_resp.status_code == 200:
                videos = [{"id": m["id"], "name": m.get("name", m["id"])} for m in vid_resp.json().get("data", [])]
                
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

@router.get("/api/studio/library")
async def studio_library(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
) -> Dict[str, Any]:
    user = effective_user(request)
    db = SessionLocal()
    try:
        q = db.query(StudioMedia).filter(StudioMedia.is_active == True)
        q = _owner_filter(q, user)
        total = q.count()
        rows = q.order_by(StudioMedia.created_at.desc()).offset(offset).limit(limit).all()
        return {
            "media": [_media_to_dict(m) for m in rows],
            "total": total,
            "offset": offset,
            "limit": limit
        }
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
    user = require_privilege(request, "can_generate_images")
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
    user = require_privilege(request, "can_generate_images")
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
    user = require_privilege(request, "can_generate_images")
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
    user = require_privilege(request, "can_generate_images")
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
