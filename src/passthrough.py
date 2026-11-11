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


# Attachments in a passthrough chat are not processed by Odysseus (no document
# text, no vision call, no image_url). Each one becomes a reference line the
# assistant resolves itself via GET /api/upload/{id} with the writer's token:
#   [Anhang: <name> | id=<upload-id> | mime=<mime> | bytes=<size>]
# The receiving side parses exactly these field rules, so a line that cannot
# satisfy them is dropped rather than sent malformed.
_ATTACHMENT_ID_RE = re.compile(r"^[0-9A-Fa-f]{32}(?:\.[A-Za-z0-9]+)?$")
_ATTACHMENT_MIME_RE = re.compile(r"^[A-Za-z0-9.+/-]+$")
_ATTACHMENT_NAME_MAX = 255
_ATTACHMENT_FALLBACK_MIME = "application/octet-stream"


def attachment_reference_line(file_info: Any) -> Optional[str]:
    """Reference line for one owner-resolved upload, or None if unusable.

    Only the sanitized stored ``name`` is used, never ``original_name``.
    """
    if not isinstance(file_info, dict):
        return None
    upload_id = str(file_info.get("id") or "")
    if not _ATTACHMENT_ID_RE.fullmatch(upload_id):
        return None
    from src.upload_handler import secure_filename

    # Re-sanitizing is idempotent for current uploads and protects against
    # legacy index rows; it guarantees no "|", "]" or line break in the name.
    name = secure_filename(str(file_info.get("name") or ""))[:_ATTACHMENT_NAME_MAX]
    mime = str(file_info.get("mime") or "").split(";", 1)[0].strip()
    if not _ATTACHMENT_MIME_RE.fullmatch(mime):
        mime = _ATTACHMENT_FALLBACK_MIME
    try:
        size = max(0, int(file_info.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    return f"[Anhang: {name} | id={upload_id} | mime={mime} | bytes={size}]"


def append_attachment_references(message: str, file_infos: list) -> str:
    """Append one reference line per attachment, after a blank line."""
    lines = [line for line in (attachment_reference_line(fi) for fi in file_infos) if line]
    if not lines:
        return message
    block = "\n".join(lines)
    return f"{message}\n\n{block}" if (message or "").strip() else block
