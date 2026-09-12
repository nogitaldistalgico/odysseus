"""The fitness coach's per-user directory stays reachable for the agent.

The coach turn in routes/chat_routes.py binds <FITNESS_DATA_DIR>/<user>/
fitness_data as the workspace and its prompt tells the model to read and
write the markdown files there. That directory sits under DATA_DIR, which the
state-directory confinement (tests/test_agent_state_dir_confinement.py)
otherwise denies, so it is registered as a readable carve-out. Everything
else under DATA_DIR must stay denied with the carve-out in place.
"""

import importlib
import os

import pytest

from src.constants import DATA_DIR, FITNESS_DATA_DIR
from src.tool_execution import (
    _agent_readable_data_subdirs,
    _resolve_tool_path,
    _resolve_tool_path_in_workspace,
    vet_workspace,
)


def _coach_workspace(user: str = "till") -> str:
    return os.path.join(FITNESS_DATA_DIR, user, "fitness_data")


def test_fitness_data_dir_is_a_carve_out_of_data_dir():
    assert os.path.dirname(FITNESS_DATA_DIR) == DATA_DIR
    assert os.path.realpath(FITNESS_DATA_DIR) in _agent_readable_data_subdirs()


@pytest.mark.parametrize("name", ["ziele.md", "wochenplan.md", "messwerte_log.md"])
def test_coach_files_resolve_through_the_default_roots(name):
    target = os.path.join(_coach_workspace(), name)
    assert _resolve_tool_path(target) == os.path.realpath(target)


def test_coach_files_resolve_inside_the_bound_workspace():
    workspace = _coach_workspace()
    assert _resolve_tool_path_in_workspace(workspace, "ziele.md") == os.path.realpath(
        os.path.join(workspace, "ziele.md")
    )


def test_coach_workspace_cannot_climb_back_into_app_state():
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path_in_workspace(_coach_workspace(), "../../../sessions.json")


@pytest.mark.parametrize("name", ["sessions.json", "auth.json", "app.db", "settings.json"])
def test_state_files_stay_denied_next_to_the_carve_out(name):
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(os.path.join(DATA_DIR, name))
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(os.path.join(FITNESS_DATA_DIR, "..", name))


def test_vet_workspace_accepts_the_coach_directory(tmp_path, monkeypatch):
    current_constants = importlib.import_module("src.constants")
    data_dir = tmp_path / "data"
    fitness_dir = data_dir / "users"
    workspace = fitness_dir / "till" / "fitness_data"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(current_constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(current_constants, "FITNESS_DATA_DIR", str(fitness_dir))
    assert vet_workspace(str(workspace)) == os.path.realpath(str(workspace))
    # The data directory itself is still refused as a workspace.
    assert vet_workspace(str(data_dir)) is None
