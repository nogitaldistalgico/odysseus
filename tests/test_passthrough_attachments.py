"""Attachments in passthrough chats become reference lines (Hugo's file store).

In a chat with a passthrough model (src/passthrough.py) Odysseus must not
process attachments itself. Each owner-resolved upload becomes one line

    [Anhang: <name> | id=<upload-id> | mime=<mime> | bytes=<size>]

after the user's text, and nothing else: no document text, no vision probe or
description, no image_url, no auto-opened document. The assistant fetches the
file itself via GET /api/upload/{id} with the writer's token. Every other
model keeps the old processing.
"""

import io
import re
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, UploadFile

import src.passthrough as passthrough
from src.attachment_refs import persistable_message_content
from src.chat_handler import ChatHandler
from src.upload_handler import UploadHandler

HUGO = "absoluter-agent"
OTHER = "gpt-4o"

LINE_RE = re.compile(
    r"^\[Anhang: [^|\]\r\n]{1,255} \| id=[0-9a-f]{32}(?:\.[A-Za-z0-9]+)? "
    r"\| mime=[A-Za-z0-9.+/-]+ \| bytes=\d+\]$"
)

IMG_ID = "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b.jpg"
STL_ID = "9b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e.stl"


def _file(upload_id, name, mime, size, **extra):
    return {
        "id": upload_id,
        "name": name,
        "original_name": extra.pop("original_name", name),
        "mime": mime,
        "size": size,
        "path": f"/nonexistent/{upload_id}",
        **extra,
    }


FILES = {
    IMG_ID: _file(IMG_ID, "IMG_4021.jpg", "image/jpeg", 2318876),
    STL_ID: _file(STL_ID, "Halter_v2.stl", "model/stl", 512084),
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf": _file(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf", "Rechnung.pdf", "application/pdf", 40960
    ),
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.docx": _file(
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.docx",
        "Brief.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        12000,
    ),
    "cccccccccccccccccccccccccccccccc.3mf": _file(
        "cccccccccccccccccccccccccccccccc.3mf", "Druck.3mf", "model/3mf", 90000
    ),
    "dddddddddddddddddddddddddddddddd.mp4": _file(
        "dddddddddddddddddddddddddddddddd.mp4", "clip.mp4", "video/mp4", 7000000
    ),
    "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.zip": _file(
        "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.zip", "archiv.zip", "application/zip", 300
    ),
}


class _OwnerCheckingUploads:
    """Resolves only uploads of `owner`; records every resolve call."""

    def __init__(self, files, owner="alice", foreign=()):
        self.files = files
        self.owner = owner
        self.foreign = set(foreign)
        self.resolved = []

    def resolve_upload(self, att_id, owner=None, **_kwargs):
        self.resolved.append((att_id, owner))
        if att_id in self.foreign or owner != self.owner:
            return None
        return self.files.get(att_id)

    def is_image_file(self, name, mime=""):
        return (mime or "").startswith("image/")


@pytest.fixture
def hugo_patterns(monkeypatch):
    monkeypatch.setattr(passthrough, "passthrough_patterns", lambda: ["absoluter-agent*"])


@pytest.fixture
def no_processing(monkeypatch):
    """Fail loudly if passthrough preprocessing reaches any processing step."""

    def _boom(what):
        def _fail(*_args, **_kwargs):
            raise AssertionError(f"{what} must not run for a passthrough chat")

        return _fail

    async def _fail_async(*_args, **_kwargs):
        raise AssertionError("YouTube fetch must not run for a passthrough chat")

    monkeypatch.setattr("src.chat_handler.model_supports_vision", _boom("vision probe"))
    monkeypatch.setattr("src.chat_handler.analyze_image_with_vl_result", _boom("vision model"))
    monkeypatch.setattr("src.chat_handler.build_user_content", _boom("build_user_content"))
    monkeypatch.setattr("src.chat_handler.extract_transcript_async", _fail_async)
    monkeypatch.setattr("src.chat_handler.fetch_youtube_comments", _fail_async)


def _handler(uploads):
    return ChatHandler(
        session_manager=None,
        memory_manager=None,
        chat_processor=None,
        research_handler=None,
        preset_manager=None,
        upload_handler=uploads,
    )


def _sess(model=HUGO, owner="alice"):
    return SimpleNamespace(model=model, endpoint_url="http://hugo/v1", owner=owner, id="chat-1")


async def _preprocess(uploads, message, att_ids, model=HUGO, allow_tools=True, docs=None):
    return await _handler(uploads).preprocess_message(
        message,
        att_ids,
        _sess(model),
        auto_opened_docs=docs if docs is not None else [],
        allow_tool_preprocessing=allow_tools,
    )


# --------------------------------------------------------------------------- #
# Fork tests 1-6 from the Hugo handoff
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_image_becomes_one_reference_line_and_nothing_else(hugo_patterns, no_processing):
    docs = []
    enhanced, user_content, text_ctx, youtube, meta = await _preprocess(
        _OwnerCheckingUploads(FILES), "Was ist auf dem Bild?", [IMG_ID], docs=docs
    )

    assert user_content == (
        "Was ist auf dem Bild?\n\n"
        f"[Anhang: IMG_4021.jpg | id={IMG_ID} | mime=image/jpeg | bytes=2318876]"
    )
    assert isinstance(user_content, str)
    assert enhanced == user_content == text_ctx
    assert youtube == []
    assert docs == []
    # The UI still gets its attachment chip metadata.
    assert [m["id"] for m in meta] == [IMG_ID]
    assert "image_url" not in user_content and "[Image" not in user_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "att_id",
    [k for k in FILES if k != IMG_ID],
)
async def test_every_file_kind_is_a_reference_line(hugo_patterns, no_processing, att_id):
    fi = FILES[att_id]
    _, user_content, _, _, _ = await _preprocess(
        _OwnerCheckingUploads(FILES), "Schau mal", [att_id]
    )

    text, line = user_content.split("\n\n")
    assert text == "Schau mal"
    assert LINE_RE.match(line), line
    assert line == f"[Anhang: {fi['name']} | id={att_id} | mime={fi['mime']} | bytes={fi['size']}]"
    assert "Attached non-text file" not in user_content


@pytest.mark.asyncio
async def test_several_attachments_keep_upload_order(hugo_patterns, no_processing):
    order = [STL_ID, IMG_ID, "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.zip"]
    _, user_content, _, _, _ = await _preprocess(_OwnerCheckingUploads(FILES), "Drei Dateien", order)

    lines = user_content.split("\n\n", 1)[1].split("\n")
    assert [re.search(r"id=(\S+)", line).group(1) for line in lines] == order
    assert all(LINE_RE.match(line) for line in lines)


@pytest.mark.asyncio
async def test_attachment_only_message_is_just_the_lines(hugo_patterns, no_processing):
    _, user_content, _, _, _ = await _preprocess(_OwnerCheckingUploads(FILES), "", [IMG_ID])
    assert LINE_RE.match(user_content)


@pytest.mark.asyncio
async def test_real_upload_with_hostile_name_stays_one_line(hugo_patterns, no_processing, tmp_path):
    """Round trip through the real UploadHandler: the line uses the sanitized name."""
    uploads = UploadHandler(str(tmp_path), str(tmp_path / "uploads"))
    record = uploads.save_upload(
        UploadFile(
            file=io.BytesIO(b"solid x\nendsolid x\n"),
            filename="a|b]\nÜber Halter.stl",
            headers=Headers({"content-type": "model/stl"}),
        ),
        client_ip="127.0.0.1",
        owner="alice",
    )

    _, user_content, _, _, _ = await _preprocess(uploads, "Wie groß?", [record["id"]])

    line = user_content.split("\n\n", 1)[1]
    assert "\n" not in line
    assert LINE_RE.match(line), line
    assert line.startswith("[Anhang: ab_Uber_Halter.stl | ")
    assert "Über" not in line and "a|b]" not in line


def test_reference_line_never_uses_original_name_and_sanitizes_legacy_rows():
    line = passthrough.attachment_reference_line(
        _file(IMG_ID, "x|y]\nz.jpg", "image/jpeg; charset=binary", "12", original_name="evil|]")
    )
    assert line == f"[Anhang: xy_z.jpg | id={IMG_ID} | mime=image/jpeg | bytes=12]"
    assert LINE_RE.match(line)


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"mime": ""}, "mime=application/octet-stream"),
        ({"mime": "image/jpeg<script>"}, "mime=application/octet-stream"),
        ({"name": ""}, "[Anhang: unnamed |"),
        ({"name": "n" * 400}, "[Anhang: " + "n" * 255 + " |"),
        ({"size": None}, "bytes=0]"),
        ({"size": "viel"}, "bytes=0]"),
    ],
)
def test_reference_line_field_rules(fields, expected):
    fi = _file(IMG_ID, "bild.jpg", "image/jpeg", 10)
    fi.update(fields)
    line = passthrough.attachment_reference_line(fi)
    assert expected in line
    assert LINE_RE.match(line)


@pytest.mark.parametrize("bad_id", ["", "../etc/passwd", "abc", IMG_ID + ".x", None])
def test_reference_line_drops_unusable_ids(bad_id):
    assert passthrough.attachment_reference_line(_file(bad_id, "a.jpg", "image/jpeg", 1)) is None


@pytest.mark.asyncio
async def test_references_arrive_on_no_tools_turns(hugo_patterns, no_processing):
    uploads = _OwnerCheckingUploads(FILES)
    _, user_content, _, _, meta = await _preprocess(
        uploads, "Ohne Werkzeuge bitte", [IMG_ID], allow_tools=False
    )

    assert user_content.endswith(f"| id={IMG_ID} | mime=image/jpeg | bytes=2318876]")
    assert uploads.resolved == [(IMG_ID, "alice")]
    assert [m["id"] for m in meta] == [IMG_ID]


@pytest.mark.asyncio
async def test_foreign_upload_yields_no_line(hugo_patterns, no_processing):
    uploads = _OwnerCheckingUploads(FILES, foreign={STL_ID})
    _, user_content, _, _, meta = await _preprocess(uploads, "Zwei", [STL_ID, IMG_ID])

    assert STL_ID not in user_content
    assert user_content.count("[Anhang: ") == 1
    assert [m["id"] for m in meta] == [IMG_ID]


@pytest.mark.asyncio
async def test_youtube_links_are_not_fetched_for_passthrough(hugo_patterns, no_processing):
    msg = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    _, user_content, _, youtube, _ = await _preprocess(_OwnerCheckingUploads(FILES), msg, [])
    assert user_content == msg
    assert youtube == []


# --------------------------------------------------------------------------- #
# Fork test 7: other models are unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_other_model_still_processes_attachments(hugo_patterns, monkeypatch):
    calls = {}

    def _vision(model, *_args, **_kwargs):
        calls["vision_probe"] = model
        return True

    def _build(message, att_ids, *_args, **_kwargs):
        calls["build_user_content"] = list(att_ids)
        return [
            {"type": "text", "text": message},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ]

    monkeypatch.setattr("src.chat_handler.model_supports_vision", _vision)
    monkeypatch.setattr("src.chat_handler.build_user_content", _build)
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)

    _, user_content, _, _, _ = await _preprocess(
        _OwnerCheckingUploads(FILES), "Was ist das?", [IMG_ID], model=OTHER
    )

    assert calls == {"vision_probe": OTHER, "build_user_content": [IMG_ID]}
    assert isinstance(user_content, list)
    assert user_content[1]["type"] == "image_url"
    assert "[Anhang:" not in user_content[0]["text"]
    assert "[Image attached: IMG_4021.jpg]" in user_content[0]["text"]


@pytest.mark.asyncio
async def test_other_model_no_tools_turn_still_ignores_attachments(hugo_patterns, monkeypatch):
    class _Refuse(_OwnerCheckingUploads):
        def resolve_upload(self, *_a, **_k):
            raise AssertionError("no-tools turns on other models must not resolve uploads")

    monkeypatch.setattr("src.chat_handler.build_user_content", lambda m, *_a, **_k: m)
    _, user_content, _, _, meta = await _preprocess(
        _Refuse(FILES), "Hi", [IMG_ID], model=OTHER, allow_tools=False
    )
    assert user_content == "Hi"
    assert meta == []


# --------------------------------------------------------------------------- #
# Fork test 8: history keeps the line verbatim, no reprocessing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reference_lines_persist_verbatim_in_history(hugo_patterns, no_processing):
    from routes.chat_helpers import PreprocessedMessage, add_user_message

    enhanced, user_content, text_ctx, youtube, meta = await _preprocess(
        _OwnerCheckingUploads(FILES), "Was ist auf dem Bild?", [IMG_ID]
    )
    added = []
    sess = SimpleNamespace(add_message=added.append)
    handler = SimpleNamespace(update_session_name_if_needed=lambda *_a: None)

    add_user_message(
        sess,
        handler,
        PreprocessedMessage(
            enhanced_message=enhanced,
            user_content=user_content,
            text_for_context=text_ctx,
            youtube_transcripts=youtube,
            attachment_meta=meta,
        ),
    )

    (msg,) = added
    assert msg.content == user_content
    stored = persistable_message_content(msg.content, msg.metadata)
    assert stored == user_content
    assert "[Attachment:" not in stored
