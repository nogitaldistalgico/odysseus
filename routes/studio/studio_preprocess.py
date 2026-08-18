"""studio_preprocess.py — Dynamic model constraint fetching, reference image
preprocessing, and payload validation for Studio video generation.

Queries OpenRouter's ``GET /api/v1/videos/models`` endpoint (cached) to learn
each model's supported sizes / resolutions / aspect ratios / durations.  Before
a generation request is dispatched, reference images are automatically resized
to a valid dimension and payload parameters are validated or corrected.

Pillow (``PIL``) is lazily imported so the server starts cleanly without it;
if missing, a warning is logged and images are sent unchanged (graceful
degradation).
"""

import io
import math
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pillow lazy import
# ---------------------------------------------------------------------------
_PIL_AVAILABLE: Optional[bool] = None


def _ensure_pil():
    """Lazily import Pillow and cache the result."""
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is None:
        try:
            from PIL import Image as _img  # noqa: F401
            _PIL_AVAILABLE = True
        except ImportError:
            _PIL_AVAILABLE = False
            logger.warning(
                "Pillow is not installed — reference-image preprocessing for "
                "video generation is disabled.  Install it with: "
                "pip install Pillow"
            )
    return _PIL_AVAILABLE


# ---------------------------------------------------------------------------
# Model constraints cache  (mirrors the existing _studio_models_cache pattern)
# ---------------------------------------------------------------------------
_video_constraints_cache: Optional[Dict[str, Any]] = None
_video_constraints_cache_time: float = 0
_CONSTRAINTS_TTL: int = 3600  # 1 hour


async def _fetch_video_model_constraints() -> Dict[str, Any]:
    """Fetch and cache per-model constraints from OpenRouter's dedicated
    video-models endpoint.  Returns a dict keyed by model ID."""
    global _video_constraints_cache, _video_constraints_cache_time

    now = time.time()
    if _video_constraints_cache and (now - _video_constraints_cache_time) < _CONSTRAINTS_TTL:
        return _video_constraints_cache

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://openrouter.ai/api/v1/videos/models")
            if resp.status_code != 200:
                logger.error(
                    "Failed to fetch video model constraints from OpenRouter: %s %s",
                    resp.status_code, resp.text[:200],
                )
                return _video_constraints_cache or {}

            models = resp.json().get("data", [])
            cache: Dict[str, Any] = {}
            for m in models:
                model_id = m.get("id")
                if not model_id:
                    continue
                cache[model_id] = {
                    "supported_resolutions": m.get("supported_resolutions", []),
                    "supported_aspect_ratios": m.get("supported_aspect_ratios", []),
                    "supported_sizes": m.get("supported_sizes", []),
                    "supported_durations": m.get("supported_durations", []),
                    "supported_frame_images": m.get("supported_frame_images"),
                    "pricing_skus": m.get("pricing_skus", {}),
                    "description": m.get("description", ""),
                    "allowed_passthrough_parameters": m.get("allowed_passthrough_parameters", []),
                    "supported_parameters": m.get("supported_parameters", []),
                }
            _video_constraints_cache = cache
            _video_constraints_cache_time = time.time()
            logger.info("Cached constraints for %d video models from OpenRouter", len(cache))
            return cache
    except Exception as e:
        logger.error("Error fetching video model constraints: %s", e)
        return _video_constraints_cache or {}


async def get_model_constraints(model_id: str) -> Dict[str, Any]:
    """Return the constraint dict for *model_id*, or an empty dict if the
    model is not found in the cache / API response."""
    cache = await _fetch_video_model_constraints()
    return cache.get(model_id, {})


# ---------------------------------------------------------------------------
# Resolution / size helpers
# ---------------------------------------------------------------------------

def _parse_size(size_str: str) -> Optional[Tuple[int, int]]:
    """Parse ``'WxH'`` into ``(width, height)``.  Returns *None* on failure."""
    try:
        parts = size_str.lower().split("x")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def _round_to_multiple(value: int, multiple: int = 64) -> int:
    """Round *value* to the nearest multiple (default 64), minimum one step."""
    return max(multiple, round(value / multiple) * multiple)


def find_best_size(
    img_width: int,
    img_height: int,
    supported_sizes: List[str],
) -> Tuple[int, int]:
    """Pick the ``(w, h)`` from *supported_sizes* closest to the source image
    dimensions.  "Closest" is defined as the smallest Euclidean distance in
    (w, h) space, with a preference for matching aspect ratio.

    If *supported_sizes* is empty or unparseable, falls back to rounding the
    source dimensions to the nearest multiple of 64.
    """
    candidates: List[Tuple[int, int]] = []
    for s in supported_sizes:
        parsed = _parse_size(s)
        if parsed:
            candidates.append(parsed)

    if not candidates:
        # Universal fallback: round to nearest multiple of 64
        return _round_to_multiple(img_width), _round_to_multiple(img_height)

    src_ratio = img_width / max(img_height, 1)

    def _score(cw: int, ch: int) -> float:
        """Lower is better.  Combines area difference and aspect-ratio
        difference so we prefer sizes that are both close in area and in
        proportion to the original."""
        cand_ratio = cw / max(ch, 1)
        ratio_diff = abs(src_ratio - cand_ratio)
        area_diff = abs(img_width * img_height - cw * ch)
        # Normalise area_diff to a comparable scale
        return ratio_diff * 1000 + math.sqrt(area_diff)

    best = min(candidates, key=lambda c: _score(c[0], c[1]))
    return best


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

def preprocess_reference_image(
    image_bytes: bytes,
    model_id_constraints: Dict[str, Any],
) -> bytes:
    """Resize and center-crop *image_bytes* (PNG/JPEG) to the nearest valid
    size defined by *model_id_constraints*.

    Returns the processed image as **PNG bytes**.  If Pillow is not installed
    or processing fails, returns the original bytes unchanged.
    """
    if not _ensure_pil():
        return image_bytes

    from PIL import Image  # guarded by _ensure_pil()

    supported_sizes = model_id_constraints.get("supported_sizes", [])

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")  # ensure 3-channel for broad compat
        src_w, src_h = img.size

        target_w, target_h = find_best_size(src_w, src_h, supported_sizes)

        if (src_w, src_h) == (target_w, target_h):
            # Already the right size — return original bytes to avoid
            # unnecessary re-encoding quality loss.
            return image_bytes

        # ------------------------------------------------------------------
        # Strategy: "resize-then-center-crop"
        #   1. Scale the image so that its *smaller* edge matches the target,
        #      preserving aspect ratio  (Lanczos / high-quality downscale).
        #   2. Center-crop the *larger* edge to exactly target dimensions.
        # ------------------------------------------------------------------
        scale = max(target_w / src_w, target_h / src_h)
        new_w = round(src_w * scale)
        new_h = round(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Center crop
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        img = img.crop((left, top, left + target_w, top + target_h))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        logger.info(
            "Preprocessed reference image: %dx%d → %dx%d for model constraints",
            src_w, src_h, target_w, target_h,
        )
        return buf.getvalue()

    except Exception as e:
        logger.warning("Failed to preprocess reference image, sending original: %s", e)
        return image_bytes


# ---------------------------------------------------------------------------
# Payload parameter validation
# ---------------------------------------------------------------------------

def _find_nearest_str(value: str, allowed: List[str]) -> Optional[str]:
    """Return *value* if it is in *allowed*, otherwise ``None``."""
    if value in allowed:
        return value
    # Try case-insensitive match
    lower_map = {a.lower(): a for a in allowed}
    return lower_map.get(value.lower())


def _find_nearest_duration(value: int, allowed: List[int]) -> int:
    """Clamp *value* to the nearest entry in *allowed*."""
    if not allowed:
        return value
    return min(allowed, key=lambda d: abs(d - value))


def _derive_aspect_ratio_from_size(w: int, h: int) -> Optional[str]:
    """Derive a common aspect-ratio string from pixel dimensions."""
    if w <= 0 or h <= 0:
        return None
    g = math.gcd(w, h)
    rw, rh = w // g, h // g
    # Map to common named ratios
    common = {
        (16, 9): "16:9", (9, 16): "9:16",
        (4, 3): "4:3", (3, 4): "3:4",
        (1, 1): "1:1",
        (21, 9): "21:9", (9, 21): "9:21",
        (3, 2): "3:2", (2, 3): "2:3",
    }
    return common.get((rw, rh), f"{rw}:{rh}")


def validate_payload_params(
    payload: Dict[str, Any],
    constraints: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate and correct *payload* parameters against *constraints* fetched
    from OpenRouter.  Mutates and returns *payload*.

    - ``resolution``: kept only if it matches a ``supported_resolutions`` value.
    - ``aspect_ratio``: kept only if it matches a ``supported_aspect_ratios``
      value; otherwise derived from the first ``supported_sizes`` entry that
      best matches, or removed.
    - ``duration``: clamped to the nearest ``supported_durations`` value.
    """
    if not constraints:
        return payload

    # --- resolution ---
    sup_res = constraints.get("supported_resolutions", [])
    if sup_res and "resolution" in payload:
        matched = _find_nearest_str(payload["resolution"], sup_res)
        if matched:
            payload["resolution"] = matched
        else:
            logger.info(
                "Removing unsupported resolution '%s'; allowed: %s",
                payload["resolution"], sup_res,
            )
            del payload["resolution"]

    # --- aspect_ratio ---
    sup_ar = constraints.get("supported_aspect_ratios", [])
    if sup_ar and "aspect_ratio" in payload:
        matched = _find_nearest_str(payload["aspect_ratio"], sup_ar)
        if matched:
            payload["aspect_ratio"] = matched
        else:
            logger.info(
                "Removing unsupported aspect_ratio '%s'; allowed: %s",
                payload["aspect_ratio"], sup_ar,
            )
            del payload["aspect_ratio"]

    # --- duration ---
    sup_dur = constraints.get("supported_durations", [])
    if sup_dur and "duration" in payload:
        payload["duration"] = _find_nearest_duration(payload["duration"], sup_dur)

    # --- input_references vs frame_images ---
    # The OpenRouter API is strict: if a video model supports frame_images (like Flux 3 Video),
    # sending input_references causes a "does not support image input references" error.
    # Conversely, older/other models might expect input_references and not frame_images.
    if "supported_frame_images" in constraints:
        sup_frame_imgs = constraints["supported_frame_images"]
        if sup_frame_imgs is not None:
            # Model explicitly supports frame_images. Remove input_references.
            if "input_references" in payload:
                logger.info("Model supports frame_images; removing input_references.")
                del payload["input_references"]
        else:
            # Model does not specify frame_images support. Remove frame_images.
            if "frame_images" in payload:
                logger.info("Model does not specify frame_images support; removing frame_images.")
                del payload["frame_images"]

    return payload


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------

def supports_real_continuation(constraints: Dict[str, Any]) -> bool:
    """Return *True* if the model supports real video continuation (sending
    an existing video as reference input, not just a single frame)."""
    
    # 1. Check pricing SKUs for video input billing
    skus = constraints.get("pricing_skus", {})
    if isinstance(skus, dict):
        keys = list(skus.keys())
    elif isinstance(skus, list):
        keys = skus
    else:
        keys = []
        
    for k in keys:
        k_lower = str(k).lower()
        if "video_input" in k_lower or "video_continuation" in k_lower or "input_video" in k_lower:
            return True

    # 2. Check supported/allowed parameters
    params = constraints.get("supported_parameters", []) + constraints.get("allowed_passthrough_parameters", [])
    for p in params:
        p_lower = str(p).lower()
        if "continuation" in p_lower or p_lower in ["input_video", "video_input", "reference_video"]:
            return True
            
    # 3. Check description for continuation-specific keywords
    desc = str(constraints.get("description", "")).lower()
    continuation_kws = ["continuation", "extend video", "video extension", "continue video", "flux 3 video"]
    if any(kw in desc for kw in continuation_kws):
        return True

    return False


def supports_video_editing(constraints: Dict[str, Any]) -> bool:
    """Return *True* if the model is a video-editing model (accepts video
    input and text instructions to modify existing footage)."""
    
    desc = str(constraints.get("description", "")).lower()
    
    # 1. Strong description keywords that definitively indicate video editing
    strong_keywords = [
        "video editing", "edit video", "video-to-video", "vid2vid",
        "modify existing video", "transform video", "video editor",
        "aleph 2"
    ]
    if any(kw in desc for kw in strong_keywords):
        return True
        
    # 2. Check supported/allowed parameters for editing flags
    params = constraints.get("supported_parameters", []) + constraints.get("allowed_passthrough_parameters", [])
    for p in params:
        p_lower = str(p).lower()
        if "edit" in p_lower or "instruction" in p_lower or "video_to_video" in p_lower:
            return True

    # 3. Fallback heuristic: models without 'supported_frame_images' (not frame-based gen) 
    # but with editing-related terms in description
    if constraints.get("supported_frame_images") is None:
        loose_keywords = ["edit", "editing", "modify", "transform", "in-context"]
        if any(kw in desc for kw in loose_keywords):
            return True
            
    return False

def supports_character_reference(constraints: Dict[str, Any]) -> bool:
    """Return *True* if the model supports character reference via multiple images.
    
    Detection heuristic: Any model that accepts images as input can theoretically 
    support character reference via injected reference images. We also check known
    series and keywords as a fallback.
    """
    arch = constraints.get("architecture", {})
    input_mods = arch.get("input_modalities") or []
    if "image" in input_mods:
        return True
        
    modality = str(arch.get("modality", "")).lower()
    if "->" in modality:
        inputs = modality.split("->")[0]
        if "image" in inputs:
            return True

    model_id = str(constraints.get("id", "")).lower()
    desc = str(constraints.get("description", "")).lower()
    
    # Known OpenRouter series that support input_references or image prompts
    known_series = ["flux", "seedance", "kling", "gemini", "runway", "luma", "midjourney", "minmax", "haiper"]
    if any(s in model_id for s in known_series):
        return True
        
    keywords = ["character reference", "multiple reference", "identity preservation", "image input", "image-to-video", "image-to-image"]
    if any(kw in desc for kw in keywords):
        return True
        
    return False
