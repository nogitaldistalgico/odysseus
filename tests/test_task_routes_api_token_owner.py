"""Task routes resolve a bearer API token to the token's owner.

The auth middleware marks token requests with ``current_user = "api"`` and
puts the real person in ``api_token_owner``. Owner-scoped task routes must use
that owner, as calendar and notes do, instead of the ``"api"`` placeholder.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.task.task_routes as task_routes


def _endpoint(router, path, method):
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _notifications_endpoint(popped):
    def _pop(owner):
        popped.append(owner)
        return []

    router = task_routes.setup_task_routes(task_scheduler=SimpleNamespace(pop_notifications=_pop))
    return _endpoint(router, "/api/tasks/notifications", "GET")


def _token_request(owner):
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user="api",
            api_token=True,
            api_token_owner=owner,
            api_token_scopes=["chat"],
        )
    )


@pytest.mark.asyncio
async def test_api_token_request_uses_token_owner():
    popped = []
    await _notifications_endpoint(popped)(_token_request("alice"))
    assert popped == ["alice"]


@pytest.mark.asyncio
async def test_api_token_without_owner_is_refused():
    popped = []
    with pytest.raises(HTTPException) as exc:
        await _notifications_endpoint(popped)(_token_request(None))
    assert exc.value.status_code == 403
    assert popped == []
