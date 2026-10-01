"""Passthrough mode for assistants that bring their own context.

A chat whose model matches one of the ``passthrough_model_patterns`` settings
(glob patterns, default ``absoluter-agent*``) is relayed as-is: Odysseus sends
only the user and assistant turns. There is no preface (memory, RAG, web
search, skills, link content, prompt-safety policy, preset prompt), no
prefetched results, no date line, no compaction and no agent mode. Background
tasks (titles, memory extraction, summaries) never run on such a model, and
every request to it carries the chat id in the ``X-Odysseus-Chat`` header.

With any other model Odysseus behaves exactly as before.
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

PASSTHROUGH_PATTERNS_SETTING = "passthrough_model_patterns"
PASSTHROUGH_CHAT_HEADER = "X-Odysseus-Chat"


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    # Only * and ? are wildcards. static/js/passthrough.js mirrors this so the
    # web UI and the server always agree on which chats pass through.
    return re.compile(re.escape(pattern).replace(r"\*", ".*").replace(r"\?", "."))


def passthrough_patterns() -> list[str]:
    """Configured model-name patterns, lower-cased. Empty disables the mode."""
    try:
        from src.settings import get_setting
        raw = get_setting(PASSTHROUGH_PATTERNS_SETTING, [])
    except Exception:
        logger.debug("Could not read %s", PASSTHROUGH_PATTERNS_SETTING, exc_info=True)
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        return []
    return [p.strip().lower() for p in raw if isinstance(p, str) and p.strip()]


def is_passthrough_model(model: Optional[str]) -> bool:
    """True when chats with ``model`` are relayed in passthrough mode.

    This is ``ist_durchreichen(modell)`` from the Hugo fork spec; every
    passthrough decision goes through here.
    """
    name = model.strip().lower() if isinstance(model, str) else ""
    if not name:
        return False
    return any(_glob_regex(p).fullmatch(name) for p in passthrough_patterns())


def apply_passthrough_chat_header(headers: dict, model: Optional[str], session_id: Any) -> None:
    """Tag a request to a passthrough model with its chat id, in place."""
    if session_id and is_passthrough_model(model):
        headers[PASSTHROUGH_CHAT_HEADER] = str(session_id)
