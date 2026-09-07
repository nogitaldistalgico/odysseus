"""Cost estimation for Studio video generations.

OpenRouter reports per-model prices in ``pricing_skus``. Across the catalogue
two computable families appear:

  * ``duration_seconds*``            — US dollars per second of output
  * ``cents_per_second*`` / ``cents_per_video_output_second*``
                                     — US cents per second of output

plus per-generation floors (``minimum_cents_per_generation``) and per-reference
surcharges (``reference_images``, ``cents_per_image_input``).

A third family, ``video_tokens*``, prices per generated video token. OpenRouter
publishes no token-count formula, so those models are reported as "unknown"
rather than guessed at — a wrong number is worse than no number.

The module is deliberately dependency-free so it can be unit-tested against a
saved catalogue without pulling in the app.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Resolution labels as they appear in SKU key suffixes.
_RES_ALIASES = {
    "480": "480p", "480p": "480p",
    "540": "540p", "540p": "540p",
    "720": "720p", "720p": "720p",
    "1024": "1024p", "1024p": "1024p",
    "1080": "1080p", "1080p": "1080p", "fhd": "1080p",
    "2k": "2k", "1440p": "2k",
    "4k": "4k", "2160p": "4k",
}


def _normalise_resolution(resolution: Optional[str]) -> Optional[str]:
    if not resolution:
        return None
    key = str(resolution).strip().lower()
    if key in _RES_ALIASES:
        return _RES_ALIASES[key]
    # "1920x1080" -> use the smaller edge, which is how the labels are named.
    if "x" in key:
        try:
            w, h = (int(p) for p in key.split("x", 1))
            return _RES_ALIASES.get(str(min(w, h)))
        except Exception:
            return None
    return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_keys(
    res: Optional[str],
    generate_audio: Optional[bool],
    has_image_input: bool,
    is_continuation: bool,
) -> list:
    """SKU keys to try, most specific first.

    Ordering matters: a model may publish both ``duration_seconds`` and
    ``duration_seconds_1080p``; picking the unqualified one would understate
    the cost at higher resolutions.
    """
    audio_parts = []
    if generate_audio is True:
        audio_parts = ["_with_audio", ""]
    elif generate_audio is False:
        audio_parts = ["_without_audio", ""]
    else:
        audio_parts = ["", "_without_audio", "_with_audio"]

    res_parts = [f"_{res}", ""] if res else [""]

    keys: list = []

    # Continuation pricing, when this is an extend job.
    if is_continuation and res:
        keys.append(f"cents_per_second_video_continuation_{res}")

    # Modality-qualified families (text_to_video_ / image_to_video_).
    modality = "image_to_video" if has_image_input else "text_to_video"
    for r in res_parts:
        if r:
            keys.append(f"{modality}_duration_seconds{r}")
    # The opposite modality is a reasonable fallback if the model only lists one.
    other = "text_to_video" if has_image_input else "image_to_video"
    for r in res_parts:
        if r:
            keys.append(f"{other}_duration_seconds{r}")

    # Plain duration families, audio- and resolution-qualified.
    for a in audio_parts:
        for r in res_parts:
            keys.append(f"duration_seconds{a}{r}")

    # Cent-denominated families.
    for r in res_parts:
        keys.append(f"cents_per_video_output_second{r}")
    for r in res_parts:
        keys.append(f"cents_per_second_output{r}")
    keys.append("cents_per_second_output")

    # De-duplicate while preserving order.
    seen = set()
    return [k for k in keys if not (k in seen or seen.add(k))]


def estimate_video_cost(
    pricing_skus: Optional[Dict[str, Any]],
    *,
    duration: Optional[float],
    resolution: Optional[str] = None,
    generate_audio: Optional[bool] = None,
    reference_image_count: int = 0,
    is_continuation: bool = False,
) -> Dict[str, Any]:
    """Estimate what one generation will cost, in USD.

    Returns ``{"usd": float|None, "basis": str, "sku": str|None}``. ``usd`` is
    None when the model prices per video token, which cannot be derived from
    the parameters alone.
    """
    skus = {str(k).lower(): v for k, v in (pricing_skus or {}).items()}
    if not skus:
        return {"usd": None, "basis": "no pricing published", "sku": None}

    res = _normalise_resolution(resolution)
    has_image_input = reference_image_count > 0

    total = 0.0
    basis = None
    used_sku = None

    if duration:
        for key in _candidate_keys(res, generate_audio, has_image_input, is_continuation):
            if key not in skus:
                continue
            rate = _to_float(skus[key])
            if rate is None:
                continue
            per_second = rate / 100.0 if key.startswith("cents_per") else rate
            total = per_second * float(duration)
            used_sku = key
            basis = f"{duration:g}s x ${per_second:.4f}/s"
            break

    if used_sku is None:
        if any(k.startswith("video_tokens") for k in skus):
            return {
                "usd": None,
                "basis": "priced per video token; no token count is available up front",
                "sku": None,
            }
        return {"usd": None, "basis": "no matching price for these parameters", "sku": None}

    # Per-reference surcharges.
    if reference_image_count > 0:
        per_ref = _to_float(skus.get("reference_images"))
        if per_ref is not None:
            total += per_ref * reference_image_count
            basis += f" + {reference_image_count} ref x ${per_ref:.4f}"
        else:
            per_ref_cents = _to_float(skus.get("cents_per_image_input"))
            if per_ref_cents is not None:
                total += (per_ref_cents / 100.0) * reference_image_count
                basis += f" + {reference_image_count} ref x ${per_ref_cents / 100.0:.4f}"

    # Per-generation floor.
    minimum = _to_float(skus.get("minimum_cents_per_generation"))
    if minimum is not None:
        floor = minimum / 100.0
        if total < floor:
            total = floor
            basis = f"minimum charge ${floor:.2f}"

    return {"usd": round(total, 4), "basis": basis, "sku": used_sku}
