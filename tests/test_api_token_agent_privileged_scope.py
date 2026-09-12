"""The agent:privileged token scope.

Upstream caps every bearer-token agent run at the non-admin tool policy and
refuses token-answered tool approvals (tests/test_api_token_tool_authority.py),
because a token is a credential the owner handed to something else. The iOS
companion app is a token holder where that premise does not hold: a person
reads the transcript there and taps the approval card. The owner says so by
setting agent:privileged on that one token. Every other bearer token keeps
upstream's cap, and the scope lifts nothing outside the agent path.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth_helpers import (
    AGENT_PRIVILEGED_SCOPE,
    is_delegated_agent_credential,
    is_delegated_credential,
)


def _bearer_request(scopes):
    return SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_owner="admin", api_token_scopes=list(scopes),
        current_user="api",
    ))


def _cookie_request():
    return SimpleNamespace(state=SimpleNamespace(api_token=False, current_user="admin"))


def test_a_plain_chat_token_is_still_capped():
    assert is_delegated_agent_credential(_bearer_request(["chat"])) is True


def test_the_privileged_scope_lifts_the_agent_cap():
    request = _bearer_request(["chat", AGENT_PRIVILEGED_SCOPE])
    assert is_delegated_agent_credential(request) is False


def test_a_browser_session_is_never_delegated():
    assert is_delegated_agent_credential(_cookie_request()) is False


def test_the_privileged_scope_does_not_make_the_token_interactive_elsewhere():
    """Session endpoint options and ownership still see a delegated credential."""
    request = _bearer_request(["chat", AGENT_PRIVILEGED_SCOPE])
    assert is_delegated_credential(request) is True


def test_a_privileged_token_may_answer_a_tool_approval_prompt():
    from routes.chat_routes import _reject_delegated_tool_approval

    _reject_delegated_tool_approval(_bearer_request(["chat", AGENT_PRIVILEGED_SCOPE]))


def test_a_plain_token_still_may_not_answer_a_tool_approval_prompt():
    from routes.chat_routes import _reject_delegated_tool_approval

    with pytest.raises(HTTPException) as raised:
        _reject_delegated_tool_approval(_bearer_request(["chat"]))

    assert raised.value.status_code == 403


def test_the_scope_is_mintable_and_implies_chat():
    """The agent runs behind the chat routes, which require the chat scope."""
    from routes.api_token_routes import ALLOWED_SCOPES, _normalize_scopes

    assert AGENT_PRIVILEGED_SCOPE in ALLOWED_SCOPES
    assert _normalize_scopes([AGENT_PRIVILEGED_SCOPE]) == ["chat", AGENT_PRIVILEGED_SCOPE]
    assert _normalize_scopes(["chat", AGENT_PRIVILEGED_SCOPE]) == ["chat", AGENT_PRIVILEGED_SCOPE]
