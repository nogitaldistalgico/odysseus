"""Web UI side of passthrough chats (src/passthrough.py, static/js/passthrough.js).

In a passthrough chat, slash commands Odysseus does not know (/claude, /gemini,
/schnell, ...) must reach the model as a normal message instead of being
answered with "Unknown command" or swapped for an autocomplete suggestion.
Driven through `node --input-type=module`; skips when `node` is missing.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_passthrough_mode import PATTERN_CASES

_REPO = Path(__file__).resolve().parent.parent
_JS = _REPO / "static" / "js"
_HAS_NODE = shutil.which("node") is not None


def _node(script):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def _function_source(path, name):
    match = re.search(rf"^function {name}\([\s\S]*?^\}}", path.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} not found in {path.name}"
    return match.group(0)


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_js_pattern_matching_agrees_with_the_server():
    cases = [[model, patterns] for model, patterns, _ in PATTERN_CASES]
    result = _node(f"""
    import {{ isPassthroughModel }} from '{(_JS / "passthrough.js").as_posix()}';
    const cases = {json.dumps(cases)};
    console.log(JSON.stringify(cases.map(([m, p]) => isPassthroughModel(m, p))));
    """)
    assert result == [expected for _, _, expected in PATTERN_CASES]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_autocomplete_enter_only_completes_what_was_typed():
    ac = _JS / "slashAutocomplete.js"
    result = _node(
        _function_source(ac, "_scoreMatch") + "\n"
        + _function_source(ac, "_completesTyped") + "\n"
        + """
    const gemini = { token: '/setup gemini', aliases: ['/setup google'], help: 'Google Gemini' };
    const newChat = { token: '/new', aliases: [], help: 'Create new chat' };
    console.log(JSON.stringify({
      geminiShown: _scoreMatch(gemini, '/gemini') > 0,
      geminiCompletes: _completesTyped(gemini, '/gemini'),
      newCompletes: _completesTyped(newChat, '/ne'),
    }));
    """
    )
    # "/gemini" pops up "/setup gemini" through its help text; Enter must not
    # swap it in for a passthrough chat. A real prefix like "/ne" still completes.
    assert result == {"geminiShown": True, "geminiCompletes": False, "newCompletes": True}


def test_unknown_commands_skip_the_typo_reply_in_passthrough_chats():
    source = (_JS / "slashCommands.js").read_text(encoding="utf-8")
    assert "export async function isPassthroughChat()" in source
    assert "settings && settings.passthrough_model_patterns" in source
    assert "const suggestions = (await isPassthroughChat()) ? [] : _fuzzyMatch(rawCmd);" in source


def test_autocomplete_enter_is_gated_for_passthrough_chats():
    source = (_JS / "slashAutocomplete.js").read_text(encoding="utf-8")
    assert "isPassthroughChat().then(on => { passthroughChat = on; }" in source
    assert "e.key === 'Enter' && passthroughChat && !_completesTyped(items[selectedIdx], v)" in source
