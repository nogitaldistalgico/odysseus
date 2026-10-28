"""Local image endpoints (Settings -> Models, model_type "image") in the Studio.

GET /api/studio/models only knew OpenRouter and POST /api/studio/generate/photo
always went to openrouter.ai, so a self-hosted FLUX server configured in the
settings never reached the Studio. Now:

* every enabled image endpoint the caller may see contributes
  "local/<endpoint_id>/<model>" entries at the front of the photo list, per
  request and never written into the OpenRouter cache — also when OpenRouter
  is down or has no key. max_image_references / supports_seed come from the
  endpoint's own GET /models, asked with its bearer key;
* a local id is generated on that endpoint's /v1/images/generations with no
  OpenRouter key, a long read timeout and the endpoint's own bearer key, with
  reference and character images as base64 input_references like OpenRouter;
* that generation is a job: generate/photo answers at once with job_status
  "pending", the image arrives in the background and GET /api/studio/jobs
  reports completed/failed. An identical resubmission gets the running job,
  and a pending photo a restart cut off is failed instead of hanging forever;
* video models from the endpoint's GET /videos/models join the video list,
  generate/video posts the OpenRouter payload to <base>/videos with the
  endpoint key, and the existing finaliser completes the job through the
  stored polling_url without ever sending the OpenRouter key there.

The handlers are called directly against an in-memory SQLite database, so the
owner/enabled/model_type filtering is the real query. No test touches the
network: every httpx client is faked.
"""
import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routes.studio.studio_helpers as sh
import routes.studio.studio_routes as sr
from core.database import Base, ModelEndpoint, StudioCharacter, StudioMedia

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
LOCAL_ID = "local/img1/flux3-klein"


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[
        ModelEndpoint.__table__, StudioMedia.__table__, StudioCharacter.__table__,
    ])
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add_all([
        ModelEndpoint(id="img1", name="Mac FLUX", base_url="http://192.168.40.142:11440",
                      api_key="sk-local", model_type="image", is_enabled=True,
                      cached_models='["flux3-klein"]', pinned_models='["flux2-klein-4b"]'),
        ModelEndpoint(id="off", name="Disabled", base_url="http://10.0.0.2/v1", model_type="image",
                      is_enabled=False, cached_models='["sdxl"]'),
        ModelEndpoint(id="llm", name="Chat", base_url="http://10.0.0.3/v1", model_type="llm",
                      is_enabled=True, cached_models='["qwen"]'),
        ModelEndpoint(id="bobs", name="Bob", base_url="http://10.0.0.4/v1", model_type="image",
                      is_enabled=True, owner="bob", cached_models='["private-flux"]'),
    ])
    db.commit()
    db.close()
    monkeypatch.setattr(sr, "SessionLocal", Session)
    monkeypatch.setattr(sr, "STUDIO_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(sh, "_local_caps_cache", {})
    monkeypatch.setattr(sr, "_local_photo_jobs", {})
    return Session


def _request(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


class _CapsClient:
    """Fake for the capability lookup: GET <base>/models."""
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        _CapsClient.calls.append((url, headers))
        data = VIDEO_CATALOG if url.endswith("/videos/models") else [
            {"id": "flux3-klein", "max_image_references": 10, "supports_seed": True},
            {"id": "flux2-klein-4b", "max_image_references": 4, "supports_seed": False},
        ]
        return httpx.Response(200, request=httpx.Request("GET", url), json={"object": "list", "data": data})


VIDEO_CATALOG = [{
    "id": "ltx-2.3", "name": "LTX-2.3",
    "supported_resolutions": ["360p", "480p"], "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
    "supported_sizes": ["640x360", "360x640"], "supported_durations": [2, 3, 4, 5],
    "supported_frame_images": ["first_frame", "last_frame"],
    "generate_audio": True, "seed": True, "pricing_skus": {},
}]


def test_models_prepends_visible_local_image_models_without_caching(db_session, monkeypatch):
    cached = {"photo": [{"id": "openai/gpt-image-1"}], "video": [{"id": "google/veo-3.1"}]}

    async def _openrouter():
        return cached

    monkeypatch.setattr(sr, "_openrouter_studio_models", _openrouter)
    monkeypatch.setattr(httpx, "AsyncClient", _CapsClient)
    monkeypatch.setattr(_CapsClient, "calls", [])

    result = asyncio.run(sr.get_studio_models(_request()))

    assert [m["id"] for m in result["photo"]] == [
        "local/img1/flux3-klein", "local/img1/flux2-klein-4b", "openai/gpt-image-1",
    ]
    assert result["photo"][:2] == [
        {
            "id": "local/img1/flux3-klein",
            "name": "flux3-klein (lokal)",
            "supports_character_reference": True,
            "max_image_references": 10,
            "supports_seed": True,
            "pricing": {},
        },
        {
            "id": "local/img1/flux2-klein-4b",
            "name": "flux2-klein-4b (lokal)",
            "supports_character_reference": True,
            "max_image_references": 4,
            "supports_seed": False,
            "pricing": {},
        },
    ]
    # Only the visible endpoint is asked, with its key.
    assert sorted(_CapsClient.calls) == [
        ("http://192.168.40.142:11440/v1/models", {"Authorization": "Bearer sk-local"}),
        ("http://192.168.40.142:11440/v1/videos/models", {"Authorization": "Bearer sk-local"}),
    ]
    assert [m["id"] for m in result["video"]] == ["local/img1/ltx-2.3", "google/veo-3.1"]
    assert result["video"][0] == {
        "id": "local/img1/ltx-2.3",
        "name": "ltx-2.3 (lokal)",
        "supported_resolutions": ["360p", "480p"],
        "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
        "supported_sizes": ["640x360", "360x640"],
        "supported_durations": [2, 3, 4, 5],
        "supported_frame_images": ["first_frame", "last_frame"],
        "supports_audio": True,
        "supports_seed": True,
        "supports_continuation": False,
        "supports_video_editing": False,
        "pricing": {},
        "supports_frame_images": True,
        "supports_character_reference": False,
        "max_image_references": 0,
    }
    assert cached["photo"] == [{"id": "openai/gpt-image-1"}]  # cache untouched
    assert cached["video"] == [{"id": "google/veo-3.1"}]

    asyncio.run(sr.get_studio_models(_request()))
    assert len(_CapsClient.calls) == 2  # catalogues briefly cached


def test_models_lists_local_models_when_openrouter_is_down(db_session, monkeypatch):
    class _DownClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "AsyncClient", _DownClient)
    monkeypatch.setattr(sr, "_studio_models_cache", None)
    monkeypatch.setattr(sr, "_studio_models_error_time", 0.0)

    result = asyncio.run(sr.get_studio_models(_request()))

    ids = [m["id"] for m in result["photo"]]
    assert ids[:2] == ["local/img1/flux3-klein", "local/img1/flux2-klein-4b"]
    # Capabilities unknown (endpoint down too): no references, seed allowed.
    assert result["photo"][0]["max_image_references"] == 0
    assert result["photo"][0]["supports_character_reference"] is False
    assert result["photo"][0]["supports_seed"] is True
    assert ids[2:] == [m["id"] for m in sr._STUDIO_MODELS_FALLBACK["photo"]]
    assert sr._studio_models_cache is None


def _patch_local_client(monkeypatch, captured, item=None, fetched=PNG):
    item = item or {"b64_json": base64.b64encode(PNG).decode()}

    class _Resp:
        status_code = 200
        text = ""
        content = fetched
        headers = {"content-type": "text/html"}

        def json(self):
            return {"data": [item]}

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *args, timeout=None, **kwargs):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            return _Resp()

        async def get(self, url, headers=None):
            captured.setdefault("fetches", []).append((url, headers))
            return _Resp()

    def _no_openrouter(db):
        raise AssertionError("a local model must not need an OpenRouter key")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(sr, "require_studio_privilege", lambda request: "alice")
    monkeypatch.setattr(sr, "get_openrouter_api_key", _no_openrouter)
    monkeypatch.setattr(sr, "load_settings", lambda: {})
    monkeypatch.setattr(sr, "get_user_setting", lambda key, user, default=None: default)


def _generate_local(req):
    """generate/photo, then let the background job finish and poll it once.
    Returns ``(response, polled)``."""
    async def _go():
        media = await sr.generate_photo(_request(), req)
        job = sr._local_photo_jobs.get(media["id"])
        if job:
            await job[0]
        return media, await sr.check_video_job(_request(), media["id"])
    return asyncio.run(_go())


def test_generate_photo_goes_to_local_endpoint(db_session, monkeypatch, tmp_path):
    captured = {}
    _patch_local_client(monkeypatch, captured)
    req = sr.PhotoGenRequest(prompt="a lighthouse", model=LOCAL_ID, size="1024x768",
                             seed=7, steps=4, negative_prompt="blurry")

    pending, media = _generate_local(req)

    # The request answers before the image exists...
    assert pending["job_status"] == "pending"
    assert pending["media_type"] == "photo"
    assert pending["id"] == media["id"]
    # ...and the job delivers it.
    assert media["job_status"] == "completed"
    assert media["file_size"] == len(PNG)

    assert captured["url"] == "http://192.168.40.142:11440/v1/images/generations"
    assert captured["json"] == {
        "model": "flux3-klein", "prompt": "a lighthouse", "n": 1,
        "response_format": "b64_json", "size": "1024x768", "seed": 7, "steps": 4,
    }
    assert captured["headers"] == {"Authorization": "Bearer sk-local"}
    assert captured["timeout"].read >= 1800

    assert media["model"] == LOCAL_ID
    assert media["filename"].endswith(".png")
    assert (tmp_path / media["filename"]).read_bytes() == PNG
    db = db_session()
    try:
        assert db.query(StudioMedia).filter_by(id=media["id"], owner="alice").one().model == LOCAL_ID
    finally:
        db.close()


@pytest.mark.parametrize("url, expected, key_sent", [
    ("/files/out.png", "http://192.168.40.142:11440/files/out.png", True),
    ("https://cdn.example.com/out.png", "https://cdn.example.com/out.png", False),
    ("https://192.168.40.142:11440/out.png", "https://192.168.40.142:11440/out.png", False),
])
def test_generate_photo_local_url_response(db_session, monkeypatch, tmp_path, url, expected, key_sent):
    captured = {}
    _patch_local_client(monkeypatch, captured, item={"url": url})
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda u, **kw: (True, ""))

    _, media = _generate_local(sr.PhotoGenRequest(prompt="x", model=LOCAL_ID))

    assert media["job_status"] == "completed"
    assert captured["fetches"] == [(expected, {"Authorization": "Bearer sk-local"} if key_sent else None)]
    # Extension from the bytes, never from the endpoint's text/html header.
    assert media["filename"].endswith(".png")
    assert (tmp_path / media["filename"]).read_bytes() == PNG


def test_generate_photo_local_rejects_non_image_bytes(db_session, monkeypatch, tmp_path):
    captured = {}
    _patch_local_client(monkeypatch, captured, item={"url": "/out"}, fetched=b"<script>alert(1)</script>")
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda u, **kw: (True, ""))

    _, media = _generate_local(sr.PhotoGenRequest(prompt="x", model=LOCAL_ID))

    assert media["job_status"] == "failed"
    assert "did not return an image" in media["error"]
    assert list(tmp_path.iterdir()) == []


def test_generate_photo_local_sends_references_and_characters(db_session, monkeypatch, tmp_path):
    from PIL import Image

    chars_dir = tmp_path / "chars"
    (chars_dir / "c1").mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(chars_dir / "c1" / "face.png")
    monkeypatch.setattr(sr, "STUDIO_CHARACTERS_DIR", str(chars_dir))
    db = db_session()
    db.add(StudioCharacter(id="c1", owner_id="alice", name="Mira", images_json='["face.png"]'))
    db.commit()
    db.close()
    monkeypatch.setattr(sr, "_get_preprocessed_base64_data_url",
                        lambda file_id, constraints, owner=None: f"data:image/png;base64,{file_id}")

    captured = {}
    _patch_local_client(monkeypatch, captured)
    req = sr.PhotoGenRequest(prompt="Mira on a beach", model=LOCAL_ID,
                             media_references=[{"id": "scene1"}], character_ids=["c1"])

    _generate_local(req)

    refs = captured["json"]["input_references"]
    assert refs[0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,scene1"}}
    assert len(refs) == 2 and refs[1]["type"] == "image_url"
    assert refs[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["json"]["prompt"].startswith("[Person A] on a beach")
    assert "reference images 2 to 2" in captured["json"]["prompt"]
    assert captured["headers"] == {"Authorization": "Bearer sk-local"}


@pytest.mark.parametrize("model", [
    "local/bobs/private-flux",   # another user's endpoint
    "local/off/sdxl",            # disabled endpoint
    "local/llm/qwen",            # not an image endpoint
    "local/img1/not-offered",    # model the endpoint does not list
])
def test_generate_photo_local_rejects_unavailable_models(db_session, monkeypatch, model):
    captured = {}
    _patch_local_client(monkeypatch, captured)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=model)))

    assert exc.value.status_code == 400
    assert "url" not in captured


def test_constraints_and_cost_are_empty_for_local_ids(monkeypatch):
    async def _no_lookup(model_id):
        raise AssertionError("local ids must not hit the OpenRouter constraints")

    monkeypatch.setattr(sr, "get_model_constraints", _no_lookup)
    monkeypatch.setattr(sr, "require_studio_privilege", lambda request: "alice")

    monkeypatch.setattr(sr, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    async def _no_video_model(db, model_id, user):
        raise HTTPException(400, "not a video model")
    monkeypatch.setattr(sr, "resolve_local_video_model", _no_video_model)

    constraints = asyncio.run(sr.get_model_constraints_endpoint(_request(), LOCAL_ID))
    assert constraints["supported_sizes"] == []
    assert constraints["supports_character_reference"] is False

    cost = asyncio.run(sr.estimate_cost(_request(), sr.CostEstimateRequest(model=LOCAL_ID)))
    assert cost["usd"] == 0.0
    assert cost["model"] == LOCAL_ID


def test_identical_resubmission_returns_the_running_job(db_session, monkeypatch):
    _patch_local_client(monkeypatch, {})
    calls = []

    async def _go():
        release = asyncio.Event()

        async def _slow_endpoint(base, headers, payload):
            calls.append(payload)
            await release.wait()
            return PNG, None

        monkeypatch.setattr(sr, "_call_local_image_endpoint", _slow_endpoint)
        first = await sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=LOCAL_ID))
        again = await sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=LOCAL_ID))
        other = await sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=LOCAL_ID, seed=3))
        release.set()
        await asyncio.gather(*(task for task, _ in list(sr._local_photo_jobs.values())))
        after = await sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=LOCAL_ID))
        await asyncio.gather(*(task for task, _ in list(sr._local_photo_jobs.values())))
        return first, again, other, after

    first, again, other, after = asyncio.run(_go())

    assert again["id"] == first["id"]       # still running: same job back
    assert other["id"] != first["id"]       # a different request is a new job
    assert after["id"] != first["id"]       # once finished, a new run is allowed
    assert len(calls) == 3


def test_pending_photo_without_a_task_is_failed_not_stuck(db_session, monkeypatch):
    monkeypatch.setattr(sr, "require_studio_privilege", lambda request: "alice")
    finalised = []

    async def _finalize(media_id):
        finalised.append(media_id)
        return False

    monkeypatch.setattr(sr, "finalize_video_job", _finalize)
    db = db_session()
    db.add_all([
        StudioMedia(id="p1", filename="p1.png", media_type="photo", owner="alice", job_status="pending"),
        StudioMedia(id="p2", filename="p2.png", media_type="photo", owner="alice", job_status="pending"),
        StudioMedia(id="v1", filename="v1.mp4", media_type="video", owner="alice", job_status="pending",
                    job_id="gen-1"),
    ])
    db.commit()
    db.close()

    polled = asyncio.run(sr.check_video_job(_request(), "p1"))
    assert polled["job_status"] == "failed"
    assert "restarted" in polled["error"]

    asyncio.run(sr.finalize_pending_video_jobs())
    db = db_session()
    try:
        assert db.get(StudioMedia, "p2").job_status == "failed"
        assert db.get(StudioMedia, "v1").job_status == "pending"
    finally:
        db.close()
    # Photos never go to the OpenRouter video finaliser; videos still do.
    assert finalised == ["v1"]


# ---------------------------------------------------------------------------
# Local video (same endpoint, OpenRouter-shaped /videos API)
# ---------------------------------------------------------------------------
VIDEO_ID = "local/img1/ltx-2.3"
POLL_URL = "http://192.168.40.142:11440/v1/videos/vid_1"
MP4_URL = "http://192.168.40.142:11440/v1/videos/vid_1/content.mp4"


def _patch_video_client(monkeypatch, captured, poll_answers=()):
    """Fake local server: /videos/models, POST /videos, the polling URL, the
    mp4 and DELETE. Every call is recorded as (method, url, headers[, json])."""
    answers = list(poll_answers)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            captured.setdefault("calls", []).append(("GET", url, headers))
            req = httpx.Request("GET", url)
            if url.endswith("/videos/models"):
                return httpx.Response(200, request=req, json={"data": VIDEO_CATALOG})
            if url == POLL_URL:
                return httpx.Response(200, request=req, json=answers.pop(0))
            if url == MP4_URL:
                return httpx.Response(200, request=req, content=b"mp4-bytes")
            return httpx.Response(404, request=req)

        async def post(self, url, json=None, headers=None):
            captured.setdefault("calls", []).append(("POST", url, headers, json))
            return httpx.Response(202, request=httpx.Request("POST", url),
                                  json={"id": "vid_1", "polling_url": POLL_URL, "status": "pending"})

        async def delete(self, url, headers=None):
            captured.setdefault("calls", []).append(("DELETE", url, headers))
            return httpx.Response(200, request=httpx.Request("DELETE", url))

    def _no_openrouter(db):
        raise AssertionError("a local video model must not touch the OpenRouter key")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(sr, "require_studio_privilege", lambda request: "alice")
    monkeypatch.setattr(sr, "get_openrouter_api_key", _no_openrouter)
    monkeypatch.setattr(sr, "load_settings", lambda: {})
    monkeypatch.setattr(sr, "get_user_setting", lambda key, user, default=None: default)
    monkeypatch.setattr(sr, "is_ffmpeg_available", lambda: False)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda u, **kw: (True, ""))
    monkeypatch.setattr(sr, "_get_preprocessed_base64_data_url",
                        lambda file_id, constraints, owner=None: f"data:image/png;base64,{file_id}")


KEY = {"Authorization": "Bearer sk-local"}


def test_generate_video_posts_to_local_endpoint(db_session, monkeypatch):
    captured = {}
    _patch_video_client(monkeypatch, captured)
    req = sr.VideoGenRequest(
        prompt="waves at dusk", model=VIDEO_ID, duration=4, resolution="480p", aspect_ratio="16:9",
        generate_audio=True, media_references=[{"id": "f1", "role": "first_frame"}], upload_method="s3",
    )

    media = asyncio.run(sr.generate_video(_request(), req))

    posts = [c for c in captured["calls"] if c[0] == "POST"]
    assert len(posts) == 1
    _, url, headers, payload = posts[0]
    assert url == "http://192.168.40.142:11440/v1/videos"
    assert headers == KEY
    assert payload == {
        "model": "ltx-2.3", "prompt": "waves at dusk",
        "frame_images": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,f1"},
                          "frame_type": "first_frame"}],
        "duration": 4, "resolution": "480p", "aspect_ratio": "16:9", "generate_audio": True,
    }
    assert media["job_status"] == "pending"
    assert media["model"] == VIDEO_ID
    db = db_session()
    try:
        assert db.get(StudioMedia, media["id"]).job_id == POLL_URL
    finally:
        db.close()


@pytest.mark.parametrize("extra", [
    {"character_ids": ["c1"]},
    {"media_references": [{"id": "r1", "role": "reference"}]},
])
def test_generate_video_local_rejects_references(db_session, monkeypatch, extra):
    captured = {}
    _patch_video_client(monkeypatch, captured)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(sr.generate_video(_request(), sr.VideoGenRequest(prompt="x", model=VIDEO_ID, **extra)))

    assert exc.value.status_code == 400
    assert not [c for c in captured["calls"] if c[0] == "POST"]


def _pending_local_video(db_session, media_id="stv_local1"):
    db = db_session()
    db.add(StudioMedia(id=media_id, filename=f"{media_id}.mp4", media_type="video", owner="alice",
                       model=VIDEO_ID, job_id=POLL_URL, job_status="pending"))
    db.commit()
    db.close()
    return media_id


def test_polling_the_stored_url_completes_a_local_video(db_session, monkeypatch, tmp_path):
    captured = {}
    _patch_video_client(monkeypatch, captured, poll_answers=[
        {"status": "in_progress", "queue_position": 2},
        {"status": "completed", "unsigned_urls": [MP4_URL]},
    ])
    media_id = _pending_local_video(db_session)

    first = asyncio.run(sr.check_video_job(_request(), media_id))
    assert first["job_status"] == "pending"
    assert first["queue_position"] == 2

    done = asyncio.run(sr.check_video_job(_request(), media_id))
    assert done["job_status"] == "completed"
    assert "queue_position" not in done
    assert (tmp_path / f"{media_id}.mp4").read_bytes() == b"mp4-bytes"
    # Polled at the stored URL and downloaded, with the endpoint's own key
    # (same host) and never the OpenRouter key.
    assert [c[:3] for c in captured["calls"]] == [
        ("GET", POLL_URL, KEY), ("GET", POLL_URL, KEY), ("GET", MP4_URL, KEY),
    ]


def test_local_video_failure_carries_the_server_error(db_session, monkeypatch):
    _patch_video_client(monkeypatch, {}, poll_answers=[{"status": "failed", "error": "out of memory"}])
    media_id = _pending_local_video(db_session)

    media = asyncio.run(sr.check_video_job(_request(), media_id))

    assert media["job_status"] == "failed"
    assert media["error"] == "out of memory"


def test_deleting_a_pending_local_video_cancels_it_upstream(db_session, monkeypatch):
    captured = {}
    _patch_video_client(monkeypatch, captured)
    media_id = _pending_local_video(db_session)

    asyncio.run(sr._delete_studio_media(_request(), media_id))

    assert ("DELETE", "http://192.168.40.142:11440/v1/videos/vid_1", KEY) in captured["calls"]
    db = db_session()
    try:
        m = db.get(StudioMedia, media_id)
        assert (m.is_active, m.job_status) == (False, "failed")
    finally:
        db.close()


@pytest.mark.parametrize("answer", [
    {"status": "cancelled"},
    {"status": "completed", "unsigned_urls": ["http://192.168.40.142:11440/v1/videos/gone.mp4"]},
])
def test_local_video_terminal_states_never_stay_pending(db_session, monkeypatch, answer):
    _patch_video_client(monkeypatch, {}, poll_answers=[answer])
    media_id = _pending_local_video(db_session)

    media = asyncio.run(sr.check_video_job(_request(), media_id))

    assert media["job_status"] == "failed"
    assert media["error"]


@pytest.mark.parametrize("code", [401, 403, 404, 307])
def test_local_video_refused_status_request_fails_the_job(db_session, monkeypatch, code):
    _patch_video_client(monkeypatch, {})

    class _Refusing(httpx.AsyncClient):  # the patched fake
        async def get(self, url, headers=None):
            return httpx.Response(code, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", _Refusing)
    media_id = _pending_local_video(db_session)

    media = asyncio.run(sr.check_video_job(_request(), media_id))

    assert media["job_status"] == "failed"
