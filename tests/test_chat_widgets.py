"""Interactive chat widgets: prompt injection, gating and the frame's CSP.

The browser half (widgetDocument.js / widgets.js) is covered by
tests/test_widget_document_js.py; the passthrough + chat_stream wiring by
tests/test_passthrough_mode.py.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

import routes.chat_helpers as chat_helpers
import src.passthrough as passthrough
from core.middleware import WIDGET_FRAME_CSP, WIDGET_FRAME_PATH, SecurityHeadersMiddleware
from src.chat_processor import ChatProcessor
from src.prompt_security import UNTRUSTED_CONTEXT_POLICY
from src.widget_prompt import WIDGET_FENCE_LANG, WIDGET_SYSTEM_PROMPT, wants_widgets

_REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# The prompt itself
# --------------------------------------------------------------------------- #

def test_prompt_teaches_the_fence_the_renderer_understands():
    assert WIDGET_FENCE_LANG == "widget"
    assert "```widget\n" in WIDGET_SYSTEM_PROMPT
    # The renderer's fence regex ends at the first ``` — the example must close
    # exactly once, or the model learns to nest fences.
    example = WIDGET_SYSTEM_PROMPT.split("```widget\n", 1)[1]
    body, _, rest = example.partition("```")
    assert "<script>" in body and "```" not in body
    assert "```widget" not in rest


def test_prompt_names_the_theme_tokens_the_widget_document_defines():
    for token in ("--fg", "--panel", "--border", "--accent", "--on-accent", "--muted", "--series-1"):
        assert token in WIDGET_SYSTEM_PROMPT
    assert "odysseus:theme" in WIDGET_SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("true", True), ("True", True), (True, True), ("1", True), ("on", True),
     ("false", False), ("", False), (None, False), (False, False), ("nope", False)],
)
def test_wants_widgets_reads_form_and_json_values(flag, expected):
    assert wants_widgets(flag) is expected


# --------------------------------------------------------------------------- #
# ChatProcessor.build_context_preface
# --------------------------------------------------------------------------- #

class _Memory:
    def load(self, owner=None):
        return []

    def increment_uses(self, ids):
        pass


class _Docs:
    rag_manager = None


def _preface(**kwargs):
    processor = ChatProcessor(memory_manager=_Memory(), personal_docs_manager=_Docs())
    preface, _, _ = processor.build_context_preface(
        message="Build me a mortgage calculator",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=False,
        use_skills=False,
        **kwargs,
    )
    return preface


def test_preface_has_no_widget_prompt_by_default():
    assert all(m["content"] != WIDGET_SYSTEM_PROMPT for m in _preface())


def test_preface_puts_widget_prompt_in_the_static_system_prefix():
    preface = _preface(use_widgets=True, preset_system_prompt="You are Odysseus.")

    contents = [m["content"] for m in preface]
    assert contents[:3] == ["You are Odysseus.", UNTRUSTED_CONTEXT_POLICY, WIDGET_SYSTEM_PROMPT]
    assert preface[2]["role"] == "system"


def test_widget_prompt_is_byte_identical_across_turns():
    first = _preface(use_widgets=True)
    second = _preface(use_widgets=True)
    assert first == second


# --------------------------------------------------------------------------- #
# build_chat_context gating
# --------------------------------------------------------------------------- #

def _context_harness(monkeypatch, model, prefs):
    captured = {"preface_calls": 0}

    async def fake_preprocess(chat_handler, message, att_ids, sess, **kwargs):
        return chat_helpers.PreprocessedMessage(
            enhanced_message=message,
            user_content=message,
            text_for_context=message,
            youtube_transcripts=[],
            attachment_meta=[],
        )

    def fake_add_user_message(sess, chat_handler, preprocessed, incognito=False):
        sess.messages.append({"role": "user", "content": preprocessed.user_content})

    async def fake_maybe_compact(sess, endpoint_url, model, messages, headers, owner=None):
        return messages, 8192, False

    def fake_build_context_preface(**kwargs):
        captured["preface_calls"] += 1
        captured["use_widgets"] = kwargs.get("use_widgets")
        return [], [], []

    monkeypatch.setattr(passthrough, "passthrough_patterns", lambda: ["absoluter-agent*"])
    monkeypatch.setattr(chat_helpers, "preprocess", fake_preprocess)
    monkeypatch.setattr(chat_helpers, "extract_preset", lambda chat_handler, preset_id: chat_helpers.PresetInfo(
        temperature=0.7, max_tokens=1024, system_prompt=None, character_name=None,
    ))
    monkeypatch.setattr(chat_helpers, "add_user_message", fake_add_user_message)
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda user: dict(prefs))
    monkeypatch.setattr(chat_helpers, "effective_user", lambda request: "till")
    monkeypatch.setattr(chat_helpers, "_normalize_model_id_from_cache", lambda sess: None)
    monkeypatch.setattr(chat_helpers, "normalize_model_id", lambda endpoint_url, model, **kwargs: None)
    monkeypatch.setattr(chat_helpers, "maybe_compact", fake_maybe_compact)
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda messages, context_length: messages)

    sess = SimpleNamespace(endpoint_url="http://model.local/v1", model=model, headers={}, messages=[])
    sess.get_context_messages = lambda: list(sess.messages)
    chat_processor = SimpleNamespace(build_context_preface=fake_build_context_preface)
    return sess, chat_processor, captured


async def _build(monkeypatch, *, model="gpt-4o", prefs=None, message="hi", **kwargs):
    sess, chat_processor, captured = _context_harness(monkeypatch, model, prefs or {})
    await chat_helpers.build_chat_context(
        sess=sess,
        request=SimpleNamespace(),
        chat_handler=SimpleNamespace(),
        chat_processor=chat_processor,
        message=message,
        session_id="chat-1",
        **kwargs,
    )
    return captured


@pytest.mark.asyncio
async def test_context_requests_widget_prompt_when_client_opts_in(monkeypatch):
    captured = await _build(monkeypatch, widgets=True)
    assert captured["use_widgets"] is True


@pytest.mark.asyncio
async def test_casual_message_keeps_the_widget_prompt_for_a_stable_prefix(monkeypatch):
    # Memory/skills drop out for "hi"; the widget block must not, or the system
    # prefix (and a local KV cache) would flip between turns.
    captured = await _build(monkeypatch, widgets=True, message="hi")
    assert captured["use_widgets"] is True


@pytest.mark.asyncio
async def test_context_omits_widget_prompt_without_client_opt_in(monkeypatch):
    captured = await _build(monkeypatch)
    assert captured["use_widgets"] is False


@pytest.mark.asyncio
async def test_user_pref_turns_widgets_off(monkeypatch):
    captured = await _build(monkeypatch, widgets=True, prefs={"widgets_enabled": False})
    assert captured["use_widgets"] is False


@pytest.mark.asyncio
async def test_passthrough_model_never_gets_the_widget_prompt(monkeypatch):
    captured = await _build(monkeypatch, model="absoluter-agent", widgets=True)
    assert captured["preface_calls"] == 0


# --------------------------------------------------------------------------- #
# The frame page's CSP
# --------------------------------------------------------------------------- #

def _static_client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=_REPO / "static"), name="static")
    return TestClient(app)


def test_widget_frame_is_served_with_the_widget_csp():
    response = _static_client().get(WIDGET_FRAME_PATH)

    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == WIDGET_FRAME_CSP
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "nonce-" not in WIDGET_FRAME_CSP
    for directive in ("default-src 'none'", "connect-src 'none'", "form-action 'none'",
                      "base-uri 'none'", "frame-ancestors 'self'"):
        assert directive in WIDGET_FRAME_CSP


def test_other_static_files_keep_the_app_csp():
    response = _static_client().get("/static/login.html")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] != WIDGET_FRAME_CSP
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_widget_csp_matches_the_browser_copy():
    js = (_REPO / "static" / "js" / "widgetDocument.js").read_text(encoding="utf-8")
    header = WIDGET_FRAME_CSP.replace("; frame-ancestors 'self'", "")
    for directive in header.split("; "):
        assert directive in js, directive


# --------------------------------------------------------------------------- #
# Companion bridge: clients can discover the capability
# --------------------------------------------------------------------------- #

def test_companion_info_advertises_widgets():
    from companion.routes import setup_companion_routes

    router = setup_companion_routes()
    info = next(r.endpoint for r in router.routes if r.path.endswith("/info"))
    body = info(SimpleNamespace(state=SimpleNamespace(current_user="till")))

    assert body["capabilities"]["widgets"] is True
