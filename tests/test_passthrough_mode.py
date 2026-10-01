"""Passthrough mode for models that bring their own context (Hugo).

A chat whose model matches ``passthrough_model_patterns`` (src/passthrough.py,
default ``absoluter-agent*``) must reach the model as a clean request: only
user/assistant turns, no Odysseus preface, no date line, no compaction, no
agent mode, no background tasks on that model, plus an ``X-Odysseus-Chat``
header. Every test pairs the passthrough case with an ordinary model that must
behave exactly as before.
"""

import asyncio
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import routes.chat_helpers as chat_helpers
import routes.chat_routes as chat_routes
import src.context_compactor as cc
import src.foreground_model_routing as foreground_model_routing
import src.llm_core as llm_core
import src.passthrough as passthrough
import src.task_endpoint as task_endpoint

HUGO = "absoluter-agent"
OTHER = "gpt-4o"
HUGO_URL = "http://192.168.1.50:8000/v1"
OTHER_URL = "https://api.other.example/v1"


@pytest.fixture
def hugo_patterns(monkeypatch):
    """Pin the shipped default so a stray settings file cannot change it."""
    monkeypatch.setattr(passthrough, "passthrough_patterns", lambda: ["absoluter-agent*"])


# --------------------------------------------------------------------------- #
# §1 is_passthrough_model — the single decision point
# --------------------------------------------------------------------------- #

# Shared with the JS mirror in tests/test_passthrough_js.py.
PATTERN_CASES = [
    ("absoluter-agent", ["absoluter-agent*"], True),
    ("absoluter-agent-gesprochen", ["absoluter-agent*"], True),
    ("absoluter-agent-aufzeichnung", ["absoluter-agent*"], True),
    ("Absoluter-Agent", ["absoluter-agent*"], True),
    (" absoluter-agent ", ["absoluter-agent*"], True),
    ("gpt-4o", ["absoluter-agent*"], False),
    ("my-absoluter-agent", ["absoluter-agent*"], False),
    ("absoluter_agent", ["absoluter-agent*"], False),
    ("", ["absoluter-agent*"], False),
    (None, ["absoluter-agent*"], False),
    ("absoluter-agent", [], False),
    ("absoluter-agent", "foo*, absoluter-agent", True),
    ("absoluter.agent", ["absoluter.agent"], True),
    ("absoluterXagent", ["absoluter.agent"], False),
    ("hugo-1", ["hugo-?"], True),
    ("hugo-12", ["hugo-?"], False),
]


def test_default_setting_relays_the_three_hugo_models():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["passthrough_model_patterns"] == ["absoluter-agent*"]


@pytest.mark.parametrize(("model", "patterns", "expected"), PATTERN_CASES)
def test_is_passthrough_model_matches_patterns(monkeypatch, model, patterns, expected):
    import src.settings as settings
    monkeypatch.setattr(settings, "get_setting", lambda key, default=None: patterns)
    assert passthrough.is_passthrough_model(model) is expected


def test_unreadable_or_malformed_setting_turns_the_mode_off(monkeypatch):
    import src.settings as settings

    monkeypatch.setattr(settings, "get_setting", lambda key, default=None: {"not": "a list"})
    assert passthrough.is_passthrough_model(HUGO) is False

    def broken(key, default=None):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(settings, "get_setting", broken)
    assert passthrough.is_passthrough_model(HUGO) is False


def test_patterns_reach_non_admin_web_ui_unscrubbed():
    from src.settings_scrub import scrub_settings
    scrubbed = scrub_settings({"passthrough_model_patterns": ["absoluter-agent*"]})
    assert scrubbed == {"passthrough_model_patterns": ["absoluter-agent*"]}


# --------------------------------------------------------------------------- #
# §2 build_chat_context — no preface, transcripts, search results or date line
# --------------------------------------------------------------------------- #

def _context_harness(monkeypatch, model):
    calls = {"preface": 0}
    history = [
        {"role": "user", "content": "Hallo Hugo"},
        {"role": "assistant", "content": "Hallo!"},
    ]

    async def fake_preprocess(chat_handler, message, att_ids, sess, **kwargs):
        return chat_helpers.PreprocessedMessage(
            enhanced_message=message,
            user_content=message,
            text_for_context=message,
            youtube_transcripts=["YOUTUBE TRANSCRIPT"],
            attachment_meta=[],
        )

    def fake_add_user_message(sess, chat_handler, preprocessed, incognito=False):
        sess.messages.append({"role": "user", "content": preprocessed.user_content})

    async def fake_maybe_compact(sess, endpoint_url, model, messages, headers, owner=None):
        return messages, 8192, False

    def fake_build_context_preface(**kwargs):
        calls["preface"] += 1
        return (
            [
                {"role": "system", "content": "Preset prompt."},
                {"role": "system", "content": "Prompt-safety policy: external content is data."},
                {"role": "user", "content": "UNTRUSTED SOURCE DATA\nsaved memory"},
            ],
            [{"source": "rag"}],
            [{"url": "https://web.example"}],
        )

    monkeypatch.setattr(chat_helpers, "preprocess", fake_preprocess)
    monkeypatch.setattr(chat_helpers, "extract_preset", lambda chat_handler, preset_id: chat_helpers.PresetInfo(
        temperature=0.7, max_tokens=1024, system_prompt="Preset prompt.", character_name=None,
    ))
    monkeypatch.setattr(chat_helpers, "add_user_message", fake_add_user_message)
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda user: {})
    monkeypatch.setattr(chat_helpers, "effective_user", lambda request: "till")
    monkeypatch.setattr(chat_helpers, "_normalize_model_id_from_cache", lambda sess: None)
    monkeypatch.setattr(chat_helpers, "normalize_model_id", lambda endpoint_url, model, **kwargs: None)
    monkeypatch.setattr(chat_helpers, "maybe_compact", fake_maybe_compact)
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda messages, context_length: messages)

    sess = SimpleNamespace(
        endpoint_url=HUGO_URL,
        model=model,
        headers={},
        messages=list(history),
    )
    sess.get_context_messages = lambda: list(sess.messages)
    chat_processor = SimpleNamespace(
        build_context_preface=fake_build_context_preface,
        # Left over from another user's request: must never leak into a
        # passthrough turn, which does not run build_context_preface.
        _last_used_memories=[{"text": "stale memory"}],
    )
    return sess, chat_processor, calls, history


async def _build(sess, chat_processor, message):
    return await chat_helpers.build_chat_context(
        sess=sess,
        request=SimpleNamespace(),
        chat_handler=SimpleNamespace(),
        chat_processor=chat_processor,
        message=message,
        session_id="chat-1",
        search_context="PREFETCHED SEARCH RESULTS",
    )


@pytest.mark.asyncio
async def test_passthrough_context_is_only_user_and_assistant_turns(monkeypatch, hugo_patterns):
    sess, chat_processor, calls, history = _context_harness(monkeypatch, HUGO)
    message = "Wie wird das Wetter morgen in Berlin?"

    ctx = await _build(sess, chat_processor, message)

    assert calls["preface"] == 0
    assert ctx.messages == history + [{"role": "user", "content": message}]
    assert ctx.preface == []
    assert ctx.rag_sources == [] and ctx.web_sources == []
    assert ctx.used_memories == []
    blob = json.dumps(ctx.messages)
    assert "Current date and time" not in blob
    assert "PREFETCHED SEARCH RESULTS" not in blob
    assert "YOUTUBE TRANSCRIPT" not in blob


@pytest.mark.asyncio
async def test_other_model_context_is_unchanged(monkeypatch, hugo_patterns):
    sess, chat_processor, calls, history = _context_harness(monkeypatch, OTHER)
    message = "Wie wird das Wetter morgen in Berlin?"

    ctx = await _build(sess, chat_processor, message)

    assert calls["preface"] == 1
    roles = [m["role"] for m in ctx.messages]
    assert roles[:2] == ["system", "system"]
    blob = json.dumps(ctx.messages)
    assert "Prompt-safety policy" in blob
    assert "Current date and time" in blob
    assert "PREFETCHED SEARCH RESULTS" in blob
    assert "YOUTUBE TRANSCRIPT" in blob
    assert ctx.messages[-1] == {"role": "user", "content": message}
    assert ctx.used_memories == [{"text": "stale memory"}]


# --------------------------------------------------------------------------- #
# §2 compaction — Odysseus never summarizes a passthrough chat
# --------------------------------------------------------------------------- #

def _long_conversation():
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 400}
        for i in range(12)
    ]


@pytest.mark.asyncio
async def test_maybe_compact_skips_passthrough_model(monkeypatch, hugo_patterns):
    monkeypatch.setattr(cc, "get_context_length", lambda *args: 100)

    def no_resolve(*args, **kwargs):
        raise AssertionError("passthrough compaction must not resolve a summary model")

    async def no_summary(*args, **kwargs):
        raise AssertionError("passthrough compaction must not call an LLM")

    monkeypatch.setattr(cc, "resolve_endpoint", no_resolve)
    monkeypatch.setattr(cc, "llm_call_async", no_summary)
    monkeypatch.setattr(cc, "_update_session_history", no_resolve)
    messages = _long_conversation()

    result, context_length, was_compacted = await cc.maybe_compact(
        object(), HUGO_URL, HUGO, messages,
    )

    assert result is messages
    assert context_length == 100
    assert was_compacted is False


@pytest.mark.asyncio
async def test_maybe_compact_still_compacts_other_models(monkeypatch, hugo_patterns):
    monkeypatch.setattr(cc, "get_context_length", lambda *args: 100)
    monkeypatch.setattr(cc, "resolve_endpoint", lambda *args, **kwargs: (None, None, None))
    summary_calls = []

    async def fake_summary(url, model, messages, **kwargs):
        summary_calls.append((url, model))
        return "summary"

    monkeypatch.setattr(cc, "llm_call_async", fake_summary)
    monkeypatch.setattr(cc, "_update_session_history", lambda *args, **kwargs: None)

    _result, _length, was_compacted = await cc.maybe_compact(
        object(), OTHER_URL, OTHER, _long_conversation(),
    )

    assert was_compacted is True
    assert summary_calls == [(OTHER_URL, OTHER)]


# --------------------------------------------------------------------------- #
# §2 agent mode — passthrough chats always take the plain chat path
# --------------------------------------------------------------------------- #

class _EmptyQuery:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _EmptyDb:
    def query(self, *args, **kwargs):
        return _EmptyQuery()

    def close(self):
        return None


class _StreamRequest:
    def __init__(self, form):
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=None))
        self.state = SimpleNamespace(current_user="till")
        self._form = {"session": "chat-1", "compare_mode": "true", **form}

    async def form(self):
        return self._form


def _chat_stream_endpoint(monkeypatch, model, message, captured):
    session = SimpleNamespace(
        endpoint_url=HUGO_URL,
        model=model,
        headers={},
        name="test",
        history=[],
        add_message=lambda msg: None,
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session,
        save_sessions=lambda: None,
    )
    context = SimpleNamespace(
        user="till",
        messages=[{"role": "user", "content": message}],
        route_messages=[{"role": "user", "content": message}],
        preprocessed=SimpleNamespace(attachment_meta=[]),
        auto_opened_docs=[],
        rag_sources=[],
        web_sources=[],
        used_memories=[],
        uploaded_files=[],
        uprefs={},
        was_compacted=False,
        context_trimmed=False,
        context_length=4096,
        context_messages_before_trim=1,
        context_messages_after_trim=1,
        context_tokens_before_trim=10,
        context_tokens_after_trim=10,
        preset=SimpleNamespace(temperature=0.2, max_tokens=128, character_name=None),
    )

    async def fake_build_context(*args, **kwargs):
        captured["agent_mode"] = kwargs.get("agent_mode")
        return context

    async def fake_chat_stream(candidates, messages, **kwargs):
        captured["path"] = "chat"
        captured["tools"] = kwargs.get("tools")
        yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_agent_stream(endpoint_url, model, messages, **kwargs):
        captured["path"] = "agent"
        yield f'data: {json.dumps({"delta": "done"})}\n\n'
        yield "data: [DONE]\n\n"

    modes = []
    monkeypatch.setattr(chat_routes, "coerce_message_and_session", lambda *args, **kwargs: (message, "chat-1"))
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda request: "till")
    monkeypatch.setattr(chat_routes, "_clear_orphaned_session_endpoint", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_recover_empty_session_model", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "_reconcile_selected_route_from_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "_enforce_chat_privileges", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "resolve_session_auth", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "get_session_mode", lambda session_id: "chat")
    monkeypatch.setattr(chat_routes, "set_session_mode", lambda session_id, mode: modes.append(mode))
    monkeypatch.setattr(chat_routes, "build_chat_context", fake_build_context)
    monkeypatch.setattr(chat_routes, "SessionLocal", _EmptyDb)
    monkeypatch.setattr(chat_routes, "_is_image_generation_session", lambda *args, **kwargs: False)
    monkeypatch.setattr(chat_routes, "stream_llm_with_fallback", fake_chat_stream)
    monkeypatch.setattr(chat_routes, "stream_agent_loop", fake_agent_stream)
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat_routes, "estimate_tokens", lambda messages: 10)
    monkeypatch.setattr(chat_routes, "accumulate_token_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(foreground_model_routing, "_load_policy_preferences", lambda owner=None: {})
    captured["modes"] = modes

    router = chat_routes.setup_chat_routes(
        session_manager,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    return next(route.endpoint for route in router.routes if route.path == "/api/chat_stream")


# Each form would put an ordinary chat into agent mode: explicitly, via plan
# mode, or by the automatic escalation on search and tool wording.
AGENT_TRIGGERS = [
    ({"mode": "agent"}, "Was steht heute an?"),
    ({"mode": "chat", "plan_mode": "true"}, "Plane meinen Umzug"),
    ({"mode": "chat", "use_web": "true"}, "Was gibt es Neues?"),
    ({"mode": "chat"}, "search the web for the latest news"),
    ({}, "Was steht heute an?"),
]


async def _run_stream(monkeypatch, model, form, message):
    captured = {}
    endpoint = _chat_stream_endpoint(monkeypatch, model, message, captured)
    response = await endpoint(_StreamRequest({**form, "message": message}))
    async for _ in response.body_iterator:
        pass
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(("form", "message"), AGENT_TRIGGERS)
async def test_passthrough_chat_never_enters_agent_mode(monkeypatch, hugo_patterns, form, message):
    captured = await _run_stream(monkeypatch, HUGO, form, message)

    assert captured["path"] == "chat"
    assert captured["tools"] is None
    assert captured["agent_mode"] is False
    assert captured["modes"] == ["chat"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("form", "message"), AGENT_TRIGGERS)
async def test_other_model_still_enters_agent_mode(monkeypatch, hugo_patterns, form, message):
    captured = await _run_stream(monkeypatch, OTHER, form, message)

    assert captured["path"] == "agent"


@pytest.mark.asyncio
async def test_other_model_plain_chat_stays_chat(monkeypatch, hugo_patterns):
    captured = await _run_stream(monkeypatch, OTHER, {"mode": "chat"}, "Erzähl mir einen Witz")

    assert captured["path"] == "chat"
    assert captured["agent_mode"] is False


# --------------------------------------------------------------------------- #
# §3 background tasks never run on a passthrough model
# --------------------------------------------------------------------------- #

def _fake_resolver(config):
    """Emulate resolve_endpoint: configured prefixes win, an unset task/utility
    setting falls back to the caller's chat model ("same as chat")."""
    calls = []

    def resolve_endpoint(prefix, fallback_url=None, fallback_model=None, fallback_headers=None, owner=None):
        calls.append(prefix)
        if prefix in config:
            return config[prefix]
        if prefix == "task" and fallback_url and fallback_model:
            return fallback_url, fallback_model, fallback_headers
        return None, None, None

    return resolve_endpoint, calls


HUGO_ROUTE = (HUGO_URL, HUGO, {"Authorization": "Bearer odysseus-till"})
OTHER_ROUTE = (OTHER_URL, OTHER, {"Authorization": "Bearer other"})


def test_task_endpoint_uses_default_model_instead_of_passthrough_chat(monkeypatch, hugo_patterns):
    resolver, _ = _fake_resolver({"default": OTHER_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)

    assert task_endpoint.resolve_task_endpoint(*HUGO_ROUTE, owner="till") == OTHER_ROUTE


def test_task_endpoint_skips_task_when_default_is_passthrough_too(monkeypatch, hugo_patterns):
    resolver, _ = _fake_resolver({"default": HUGO_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)

    assert task_endpoint.resolve_task_endpoint(*HUGO_ROUTE, owner="till") == (None, None, None)


def test_task_endpoint_never_uses_a_configured_passthrough_utility(monkeypatch, hugo_patterns):
    resolver, _ = _fake_resolver({"task": HUGO_ROUTE, "default": OTHER_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)

    assert task_endpoint.resolve_task_endpoint(owner="till") == OTHER_ROUTE


def test_task_endpoint_unchanged_for_other_models(monkeypatch, hugo_patterns):
    resolver, calls = _fake_resolver({"default": HUGO_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)

    assert task_endpoint.resolve_task_endpoint(*OTHER_ROUTE, owner="till") == OTHER_ROUTE
    assert calls == ["task"]


def test_task_candidates_drop_every_passthrough_route(monkeypatch, hugo_patterns):
    utility = ("https://utility.example/v1", "utility-model", {})
    resolver, _ = _fake_resolver({"utility": utility, "default": HUGO_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)
    monkeypatch.setattr(task_endpoint, "resolve_utility_fallback_candidates", lambda owner=None: [
        HUGO_ROUTE,
        ("https://backup.example/v1", "backup-model", {}),
    ])

    candidates = task_endpoint.resolve_task_candidates(*HUGO_ROUTE, owner="till")

    assert [model for _, model, _ in candidates] == ["utility-model", "backup-model"]


@pytest.mark.asyncio
async def test_auto_name_never_asks_a_passthrough_model(monkeypatch, hugo_patterns):
    resolver, _ = _fake_resolver({"default": HUGO_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)

    # auto_name_session swallows exceptions, so record calls instead of raising.
    llm_calls = []

    async def fake_llm(url, model, messages, **kwargs):
        llm_calls.append(model)
        return "Licht im Flur"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    sess = SimpleNamespace(
        id="chat-1",
        owner="till",
        endpoint_url=HUGO_URL,
        model=HUGO,
        headers={},
        history=[SimpleNamespace(role="user", content="Mach das Licht im Flur an")],
    )
    renamed = []
    session_manager = SimpleNamespace(update_session_name=lambda *args: renamed.append(args))

    await chat_helpers.auto_name_session(session_manager, sess)

    assert llm_calls == []
    assert renamed == []


@pytest.mark.asyncio
async def test_auto_name_uses_default_model_for_passthrough_chat(monkeypatch, hugo_patterns):
    resolver, _ = _fake_resolver({"default": OTHER_ROUTE})
    monkeypatch.setattr(task_endpoint, "resolve_endpoint", resolver)
    llm_calls = []

    async def fake_llm(url, model, messages, **kwargs):
        llm_calls.append(model)
        return "Licht im Flur"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    sess = SimpleNamespace(
        id="chat-1",
        owner="till",
        endpoint_url=HUGO_URL,
        model=HUGO,
        headers={},
        history=[SimpleNamespace(role="user", content="Mach das Licht im Flur an")],
    )
    renamed = []
    session_manager = SimpleNamespace(update_session_name=lambda *args: renamed.append(args))

    await chat_helpers.auto_name_session(session_manager, sess)

    assert llm_calls == [OTHER]
    assert renamed == [("chat-1", "Licht im Flur")]


def _post_response_jobs(monkeypatch, model):
    memory_mod = types.ModuleType("services.memory.memory_extractor")

    async def fake_extract_and_store(*args, **kwargs):
        return None

    memory_mod.extract_and_store = fake_extract_and_store
    monkeypatch.setitem(sys.modules, "services.memory.memory_extractor", memory_mod)
    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint", lambda url, model, headers, owner=None: (url, model, headers))

    queued = []

    def fake_spawn(coro):
        queued.append(coro)
        coro.close()

    monkeypatch.setattr(chat_helpers, "_spawn_bg", fake_spawn)
    monkeypatch.setattr(chat_helpers, "needs_auto_name", lambda name: False)
    names = []

    def fake_runner(session_id, jobs, max_wait_s=120.0):
        names.extend(name for name, _ in jobs)
        for _, job in jobs:
            job.close()

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(chat_helpers, "_run_extraction_jobs_sequentially", fake_runner)
    sess = SimpleNamespace(
        endpoint_url=HUGO_URL,
        model=model,
        headers={},
        history=[object()] * 8,  # every 4th message pair is extraction-eligible
        name="Titel",
    )

    chat_helpers.run_post_response_tasks(
        sess, SimpleNamespace(save_sessions=lambda: None), "chat-1", "hallo", "hi", None,
        {"auto_memory": True}, memory_manager=MagicMock(), memory_vector=MagicMock(),
        webhook_manager=None, owner="till",
    )
    return names


def test_memory_extraction_is_off_for_passthrough_chats(monkeypatch, hugo_patterns):
    assert _post_response_jobs(monkeypatch, HUGO) == []


def test_memory_extraction_still_runs_for_other_models(monkeypatch, hugo_patterns):
    assert _post_response_jobs(monkeypatch, OTHER) == ["memory"]


# --------------------------------------------------------------------------- #
# §4 X-Odysseus-Chat header on every request to a passthrough model
# --------------------------------------------------------------------------- #

class _FakeStreamResp:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices": [{"delta": {"content": "hi"}}]}'
        yield "data: [DONE]"

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, captured, headers):
        self._captured = captured
        self._headers = headers

    async def __aenter__(self):
        self._captured.append(dict(self._headers or {}))
        return _FakeStreamResp()

    async def __aexit__(self, *args):
        return False


class _FakeStreamClient:
    def __init__(self, captured):
        self._captured = captured

    def stream(self, method, url, json=None, headers=None, **kwargs):
        return _FakeStreamCtx(self._captured, headers)


def _stream_headers(monkeypatch, model, session_id):
    captured = []
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeStreamClient(captured))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *args, **kwargs: None)

    async def drain():
        async for _ in llm_core.stream_llm(
            HUGO_URL, model, [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer odysseus-till"}, session_id=session_id,
        ):
            pass

    asyncio.run(drain())
    assert len(captured) == 1
    return captured[0]


def test_stream_to_passthrough_model_carries_chat_header(monkeypatch, hugo_patterns):
    headers = _stream_headers(monkeypatch, HUGO, "chat-1")
    assert headers["X-Odysseus-Chat"] == "chat-1"
    assert headers["Authorization"] == "Bearer odysseus-till"


def test_stream_to_other_model_has_no_chat_header(monkeypatch, hugo_patterns):
    headers = _stream_headers(monkeypatch, "local-model", "chat-1")
    assert "X-Odysseus-Chat" not in headers


def test_stream_without_session_has_no_chat_header(monkeypatch, hugo_patterns):
    headers = _stream_headers(monkeypatch, HUGO, None)
    assert "X-Odysseus-Chat" not in headers


class _FakeResponse:
    is_success = True
    status_code = 200
    text = ""

    def json(self):
        return {"model": HUGO, "choices": [{"message": {"content": "ok"}}]}


def _call_headers(monkeypatch, model, session_id, caller_headers):
    captured = []

    async def fake_post(client, url, headers, **kwargs):
        captured.append(dict(headers))
        return _FakeResponse()

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: None)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)
    monkeypatch.setattr(llm_core, "_set_cached_response", lambda *args, **kwargs: None)

    asyncio.run(llm_core.llm_call_async(
        HUGO_URL, model, [{"role": "user", "content": "hi"}],
        headers=caller_headers, session_id=session_id,
    ))
    assert len(captured) == 1
    return captured[0]


def test_call_to_passthrough_model_carries_chat_header(monkeypatch, hugo_patterns):
    caller_headers = {"Authorization": "Bearer odysseus-till"}
    headers = _call_headers(monkeypatch, HUGO, "chat-2", caller_headers)
    assert headers["X-Odysseus-Chat"] == "chat-2"
    # The session's stored headers are never mutated.
    assert caller_headers == {"Authorization": "Bearer odysseus-till"}


def test_call_to_other_model_has_no_chat_header(monkeypatch, hugo_patterns):
    headers = _call_headers(monkeypatch, "local-model", "chat-2", {})
    assert "X-Odysseus-Chat" not in headers
