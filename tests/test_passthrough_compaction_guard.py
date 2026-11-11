"""Manual compaction never touches a passthrough chat or uses a passthrough model.

Without a utility model, /compact fell back to the chat's own model. In a
passthrough chat (src/passthrough.py) that sent a summary prompt to the
assistant, and an empty answer would have replaced the history.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.history.history_routes as history_routes
import routes.session_routes as session_routes
import src.passthrough as passthrough
from core.models import ChatMessage

HUGO = "absoluter-agent"
OTHER = "gpt-4o"


@pytest.fixture(autouse=True)
def hugo_patterns(monkeypatch):
    monkeypatch.setattr(passthrough, "passthrough_patterns", lambda: ["absoluter-agent*"])


def _session(model):
    history = [ChatMessage("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(12)]
    return SimpleNamespace(
        id="chat-1", model=model, endpoint_url="http://hugo/v1", headers={}, owner="alice",
        history=history, message_count=len(history),
        get_context_messages=lambda: [],
    )


def _history_compact(monkeypatch, sess, utility_model=None):
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda *_a, **_k: None)
    monkeypatch.setattr(history_routes, "_reject_compact_during_active_run", lambda *_a: None)
    monkeypatch.setattr("src.model_context.get_context_length", lambda *_a, **_k: 8192)

    async def _no_llm(*_a, **_k):
        raise AssertionError("no summary model may be called")

    monkeypatch.setattr("src.llm_core.llm_call_async", _no_llm)
    if utility_model:
        monkeypatch.setattr(
            "src.endpoint_resolver.resolve_endpoint",
            lambda *_a, **_k: ("http://hugo/v1", utility_model, {}),
        )
    sm = SimpleNamespace(get_session=lambda _sid: sess)
    router = history_routes.setup_history_routes(sm)
    endpoint = next(
        r.endpoint for r in router.routes
        if r.path == "/api/session/{session_id}/compact" and "POST" in r.methods
    )
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice", api_token=False))
    return endpoint(request, "chat-1")


@pytest.mark.asyncio
async def test_history_compact_refuses_passthrough_chat(monkeypatch):
    sess = _session(HUGO)
    before = list(sess.history)
    with pytest.raises(HTTPException) as exc:
        await _history_compact(monkeypatch, sess)
    assert exc.value.status_code == 400
    assert sess.history == before


@pytest.mark.asyncio
async def test_history_compact_never_uses_passthrough_utility(monkeypatch):
    sess = _session(OTHER)
    before = list(sess.history)
    with pytest.raises(HTTPException) as exc:
        await _history_compact(monkeypatch, sess, utility_model=HUGO)
    assert exc.value.status_code == 400
    assert sess.history == before


def test_session_compact_guard_refuses_passthrough_chat():
    with pytest.raises(HTTPException) as exc:
        session_routes._reject_passthrough_compaction(_session(HUGO))
    assert exc.value.status_code == 400
    session_routes._reject_passthrough_compaction(_session(OTHER))


def test_session_compact_route_runs_the_guard_and_model_check():
    src = open(session_routes.__file__, encoding="utf-8").read()
    body = src.split("async def compact_session(", 1)[1].split("\n    @router.", 1)[0]
    assert "_reject_passthrough_compaction(session)" in body
    assert "or is_passthrough_model(model):" in body
