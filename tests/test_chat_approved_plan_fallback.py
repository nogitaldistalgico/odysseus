"""The approved_plan fallback for clients that only send a confirmation.

The WebUI posts the approved checklist back as approved_plan on every turn.
The iOS client answers a proposed plan with plain text ("ja", "ok"), so the
server reads the checklist off the last assistant turn. The first version of
this fallback dereferenced the session ID string and ran before a JSON
body's message was resolved, so a bare "ok" in the WebUI was a 500 and the
iOS client never reached it.
"""

from types import SimpleNamespace

import pytest

from routes.chat_routes import _approved_plan_from_confirmation


def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


PLAN = "Here is the plan:\n- [ ] step one\n- [ ] step two\nShall I go?"


def _session_with_plan():
    return SimpleNamespace(history=[_msg("user", "plan it"), _msg("assistant", PLAN)])


@pytest.mark.parametrize("confirmation", ["ja", "Ok!", "yes", "Mach das.", " go ahead "])
def test_a_bare_confirmation_recovers_the_checklist(confirmation):
    assert _approved_plan_from_confirmation(confirmation, _session_with_plan()) == (
        "- [ ] step one\n- [ ] step two\nShall I go?"
    )


@pytest.mark.parametrize("message", ["ja aber anders", "", None, "what is step one?"])
def test_anything_but_a_bare_confirmation_is_ignored(message):
    assert _approved_plan_from_confirmation(message, _session_with_plan()) == ""


def test_no_checklist_in_the_last_assistant_turn_means_no_plan():
    session = SimpleNamespace(history=[_msg("assistant", "no checklist here")])
    assert _approved_plan_from_confirmation("ok", session) == ""


def test_a_session_id_string_or_empty_history_does_not_crash():
    assert _approved_plan_from_confirmation("ok", "session-id") == ""
    assert _approved_plan_from_confirmation("ok", SimpleNamespace(history=[])) == ""


def test_the_recovered_plan_is_capped_like_the_explicit_field():
    session = SimpleNamespace(history=[_msg("assistant", "- [ ] " + "x" * 20000)])
    assert len(_approved_plan_from_confirmation("yes", session)) == 8192
