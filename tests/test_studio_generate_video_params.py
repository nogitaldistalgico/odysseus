"""POST /api/studio/generate/video must forward the generation parameters —
but only the ones the model publishes a list for.

VideoGenRequest accepted duration / resolution / aspect_ratio / generate_audio
from the start, and the /api/studio/models constraints exist so clients can
offer exactly those choices — but generate_video() built its OpenRouter
payload from model, prompt and the image inputs only, so every choice was
silently replaced by the provider default. (extend_video() always forwarded
them.)

Forwarding everything blindly broke models such as runway/aleph-2, which list
no durations or resolutions: clients carry values over from a previously
selected model, validate_payload_params only clamps against a non-empty list,
and OpenRouter rejects a parameter the model does not take. So an unlisted
parameter is dropped (the pre-forwarding behaviour) and a listed one is
forwarded and clamped.

The handler is called directly with its collaborators monkeypatched, the same
way tests/test_ai_image_url_safety.py drives the image generator; the fake
httpx client captures the payload that would have gone to OpenRouter.
"""
import httpx

import routes.studio.studio_routes as sr


class _SubmitResponse:
    status_code = 202
    text = ""

    def json(self):
        return {"id": "gen-1", "polling_url": "https://openrouter.ai/api/v1/videos/gen-1"}


class _FakeSession:
    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def close(self):
        pass


def _patch(monkeypatch, constraints, captured):
    async def _post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        return _SubmitResponse()

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        post = _post

    async def _constraints(model_id):
        return constraints

    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(sr, "require_studio_privilege", lambda request: "tester")
    monkeypatch.setattr(sr, "get_openrouter_api_key", lambda db: "sk-test")
    monkeypatch.setattr(sr, "load_settings", lambda: {})
    monkeypatch.setattr(sr, "get_user_setting", lambda key, user, default=None: default)
    monkeypatch.setattr(sr, "get_model_constraints", _constraints)
    monkeypatch.setattr(sr, "SessionLocal", _FakeSession)


_VEO_LIKE = {
    "supported_durations": [4, 8],
    "supported_resolutions": ["720p", "1080p"],
    "supported_aspect_ratios": ["16:9", "9:16"],
    "generate_audio": True,
}


async def test_generate_video_forwards_duration_resolution_aspect_and_audio(monkeypatch):
    captured = {}
    _patch(monkeypatch, _VEO_LIKE, captured)

    result = await sr.generate_video(
        object(),
        sr.VideoGenRequest(
            prompt="a slow push-in on a lighthouse",
            model="google/veo-3.1",
            duration=8,
            resolution="720p",
            aspect_ratio="16:9",
            generate_audio=False,
        ),
    )

    assert captured["url"] == "https://openrouter.ai/api/v1/videos"
    payload = captured["json"]
    assert payload["model"] == "google/veo-3.1"
    assert payload["prompt"] == "a slow push-in on a lighthouse"
    assert payload["duration"] == 8
    assert payload["resolution"] == "720p"
    assert payload["aspect_ratio"] == "16:9"
    assert payload["generate_audio"] is False
    # Nothing else sneaks in when no images are attached.
    assert "input_references" not in payload and "frame_images" not in payload
    # The job is stored as pending with the polling URL as its job id.
    assert result["job_status"] == "pending"
    assert result["job_id"] == "https://openrouter.ai/api/v1/videos/gen-1"


async def test_generate_video_omits_unset_params(monkeypatch):
    captured = {}
    _patch(monkeypatch, _VEO_LIKE, captured)

    await sr.generate_video(object(), sr.VideoGenRequest(prompt="rain on a window", model="m/x"))

    payload = captured["json"]
    assert set(payload) == {"model", "prompt"}


async def test_generate_video_drops_params_the_model_does_not_list(monkeypatch):
    """runway/aleph-2 shape: aspect ratios listed, no durations / resolutions,
    no audio. A client that still sends the lot (stale values from the model
    it had selected before) must not have them forwarded."""
    captured = {}
    _patch(
        monkeypatch,
        {"supported_aspect_ratios": ["16:9", "9:16"], "supported_durations": None, "supported_resolutions": None, "generate_audio": False},
        captured,
    )

    await sr.generate_video(
        object(),
        sr.VideoGenRequest(prompt="x", model="runway/aleph-2", duration=8, resolution="720p", aspect_ratio="16:9", generate_audio=True),
    )

    payload = captured["json"]
    assert set(payload) == {"model", "prompt", "aspect_ratio"}
    assert payload["aspect_ratio"] == "16:9"


async def test_generate_video_unlisted_model_sends_nothing_extra(monkeypatch):
    """No constraints at all (model missing from /videos/models): behave as
    before the forwarding existed."""
    captured = {}
    _patch(monkeypatch, {}, captured)

    await sr.generate_video(
        object(), sr.VideoGenRequest(prompt="x", model="m/x", duration=8, resolution="720p", aspect_ratio="16:9", generate_audio=False),
    )

    assert set(captured["json"]) == {"model", "prompt"}


async def test_generate_video_params_are_clamped_to_model_constraints(monkeypatch):
    captured = {}
    _patch(
        monkeypatch,
        {"supported_durations": [4, 8], "supported_resolutions": ["720p"], "supported_aspect_ratios": ["16:9", "9:16"]},
        captured,
    )

    await sr.generate_video(
        object(),
        sr.VideoGenRequest(prompt="x", model="m/x", duration=7, resolution="4k", aspect_ratio="9:16"),
    )

    payload = captured["json"]
    assert payload["duration"] == 8          # nearest supported value
    assert "resolution" not in payload       # unsupported → dropped, not sent blindly
    assert payload["aspect_ratio"] == "9:16"  # supported → kept
