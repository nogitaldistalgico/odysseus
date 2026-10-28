"""Local image endpoints (Settings -> Models, model_type "image") in the Studio.

GET /api/studio/models only knew OpenRouter and POST /api/studio/generate/photo
always went to openrouter.ai, so a self-hosted FLUX server configured in the
settings never reached the Studio. Now:

* every enabled image endpoint the caller may see contributes
  "local/<endpoint_id>/<model>" entries at the front of the photo list, per
  request and never written into the OpenRouter cache — also when OpenRouter
  is down or has no key;
* a local id is generated on that endpoint's /v1/images/generations with no
  OpenRouter key, a long read timeout and the endpoint's own bearer key.

The handlers are called directly against an in-memory SQLite database, so the
owner/enabled/model_type filtering is the real query.
"""
import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routes.studio.studio_routes as sr
from core.database import Base, ModelEndpoint, StudioMedia

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
LOCAL_ID = "local/img1/flux3-klein"


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=[ModelEndpoint.__table__, StudioMedia.__table__])
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
    return Session


def _request(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def test_models_prepends_visible_local_image_models_without_caching(db_session, monkeypatch):
    cached = {"photo": [{"id": "openai/gpt-image-1"}], "video": [{"id": "google/veo-3.1"}]}

    async def _openrouter():
        return cached

    monkeypatch.setattr(sr, "_openrouter_studio_models", _openrouter)

    result = asyncio.run(sr.get_studio_models(_request()))

    assert [m["id"] for m in result["photo"]] == [
        "local/img1/flux3-klein", "local/img1/flux2-klein-4b", "openai/gpt-image-1",
    ]
    assert result["photo"][0] == {
        "id": "local/img1/flux3-klein",
        "name": "flux3-klein (lokal)",
        "supports_character_reference": False,
        "max_image_references": 0,
        "supports_seed": True,
        "pricing": {},
    }
    assert result["video"] == cached["video"]
    assert cached["photo"] == [{"id": "openai/gpt-image-1"}]  # cache untouched


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


def test_generate_photo_goes_to_local_endpoint(db_session, monkeypatch, tmp_path):
    captured = {}
    _patch_local_client(monkeypatch, captured)
    req = sr.PhotoGenRequest(prompt="a lighthouse", model=LOCAL_ID, size="1024x768",
                             seed=7, steps=4, negative_prompt="blurry")

    media = asyncio.run(sr.generate_photo(_request(), req))

    assert captured["url"] == "http://192.168.40.142:11440/v1/images/generations"
    assert captured["json"] == {
        "model": "flux3-klein", "prompt": "a lighthouse", "n": 1,
        "response_format": "b64_json", "size": "1024x768", "seed": 7, "steps": 4,
    }
    assert captured["headers"] == {"Authorization": "Bearer sk-local"}
    assert captured["timeout"].read >= 900

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

    media = asyncio.run(sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=LOCAL_ID)))

    assert captured["fetches"] == [(expected, {"Authorization": "Bearer sk-local"} if key_sent else None)]
    # Extension from the bytes, never from the endpoint's text/html header.
    assert media["filename"].endswith(".png")
    assert (tmp_path / media["filename"]).read_bytes() == PNG


def test_generate_photo_local_rejects_non_image_bytes(db_session, monkeypatch, tmp_path):
    captured = {}
    _patch_local_client(monkeypatch, captured, item={"url": "/out"}, fetched=b"<script>alert(1)</script>")
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda u, **kw: (True, ""))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(sr.generate_photo(_request(), sr.PhotoGenRequest(prompt="x", model=LOCAL_ID)))

    assert exc.value.status_code == 502
    assert list(tmp_path.iterdir()) == []


def test_generate_photo_local_rejects_references(db_session, monkeypatch):
    captured = {}
    _patch_local_client(monkeypatch, captured)
    req = sr.PhotoGenRequest(prompt="x", model=LOCAL_ID, character_ids=["c1"])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(sr.generate_photo(_request(), req))

    assert exc.value.status_code == 400
    assert "reference" in exc.value.detail
    assert "url" not in captured


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

    constraints = asyncio.run(sr.get_model_constraints_endpoint(LOCAL_ID))
    assert constraints["supported_sizes"] == []
    assert constraints["supports_character_reference"] is False

    cost = asyncio.run(sr.estimate_cost(_request(), sr.CostEstimateRequest(model=LOCAL_ID)))
    assert cost["usd"] == 0.0
    assert cost["model"] == LOCAL_ID
