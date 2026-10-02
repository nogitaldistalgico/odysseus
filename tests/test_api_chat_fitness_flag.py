"""/api/chat reads the fitness-coach flag from its JSON body.

It used to read `form_data`/`body`, which only exist in /api/chat_stream, so
every non-streaming chat crashed with NameError after building its context.
"""
from types import SimpleNamespace

import pytest

import routes.chat_routes as chat_routes
import src.foreground_model_routing as foreground_model_routing
import src.llm_core as llm_core
from src.request_models import ChatRequest


class _ChatHandler:
    async def handle_memory_command(self, sess, message):
        return None


async def _call_api_chat(monkeypatch, *, is_fitness_coach):
    sent = []
    session = SimpleNamespace(
        endpoint_url="https://selected.example/v1",
        model="selected-model",
        headers={},
        history=[],
        add_message=lambda message: None,
    )
    context = SimpleNamespace(
        user="till",
        messages=[{"role": "user", "content": "Wie lief mein Training?"}],
        route_messages=[],
        preface=[],
        context_length=100,
        uprefs={},
        preset=SimpleNamespace(temperature=0.2, max_tokens=128, character_name=None),
    )

    async def fake_build_context(*args, **kwargs):
        return context

    async def fake_llm(url, model, messages, **kwargs):
        sent.append(list(messages))
        return "ok"

    import core.database as database
    import services.fitness.coach as coach

    monkeypatch.setattr(coach, "build_system_prompt", lambda workspace: "FITNESS COACH PROMPT")
    monkeypatch.setattr(database, "update_session_last_accessed", lambda session_id: None)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: {})
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda request: "till")
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "build_chat_context", fake_build_context)
    monkeypatch.setattr(chat_routes, "clean_thinking_for_save", lambda reply, metadata: (reply, metadata))
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: None)

    router = chat_routes.setup_chat_routes(
        SimpleNamespace(get_session=lambda session_id: session, save_sessions=lambda: None),
        _ChatHandler(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/chat")
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        state=SimpleNamespace(current_user="till"),
    )
    response = await endpoint(request, ChatRequest(
        message="Wie lief mein Training?",
        session="session-1",
        is_fitness_coach=is_fitness_coach,
    ))
    return response, sent


@pytest.mark.asyncio
async def test_api_chat_injects_the_coach_prompt_when_flagged(monkeypatch):
    response, sent = await _call_api_chat(monkeypatch, is_fitness_coach=True)

    assert response["response"] == "ok"
    assert sent[0][0] == {"role": "system", "content": "FITNESS COACH PROMPT"}


@pytest.mark.asyncio
async def test_api_chat_without_the_flag_is_a_plain_chat(monkeypatch):
    response, sent = await _call_api_chat(monkeypatch, is_fitness_coach=False)

    assert response["response"] == "ok"
    assert all(message.get("content") != "FITNESS COACH PROMPT" for message in sent[0])
