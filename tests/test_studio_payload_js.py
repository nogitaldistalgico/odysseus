"""Pin the DOM-free request builders in static/js/studio/payload.js.

The Media Studio WebUI maps composer state onto the request models in
routes/studio/studio_routes.py (PhotoGenRequest, VideoGenRequest,
VideoExtendRequest, VideoEditRequest, CostEstimateRequest). These tests lock
the contract so a UI refactor cannot silently drop a field the backend reads.

Driven through `node --input-type=module` like test_esc_menu_stack_js.py; the
module has no imports so its source is inlined verbatim. Skips when `node` is
not installed rather than failing.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "studio" / "payload.js"
_HAS_NODE = shutil.which("node") is not None
_SRC = _MODULE.read_text(encoding="utf-8") if _MODULE.exists() else ""
# Strip the `export` keywords so the module body can be eval'd inline.
_INLINE = re.sub(r"^export\s+(?=(const|function|let|class)\b)", "", _SRC, flags=re.M)


def _run(body: str):
    js = _INLINE + "\nconst print = (...a) => console.log(...a);\n" + body
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def test_module_exists():
    assert _MODULE.exists()


def test_photo_request_matches_backend_model():
    body = """
    const req = buildPhotoRequest({ prompt: '  a cat ', negativePrompt: 'dog', model: 'p1', size: '1024x1024',
      seed: '42', steps: '30', references: [{ id: 'a.png', role: 'reference' }, { id: 'st_1', role: 'first_frame' }, { id: '' }],
      characterIds: ['c1'], characterTemplate: 'T {pseudo}' }, {});
    console.log(JSON.stringify(req));
    """
    assert _run(body) == {
        "prompt": "a cat", "model": "p1", "negative_prompt": "dog", "size": "1024x1024",
        "seed": 42, "steps": 30,
        "media_references": [{"id": "a.png", "role": "reference"}, {"id": "st_1", "role": "first_frame"}],
        "character_ids": ["c1"], "character_mapping_template": "T {pseudo}",
    }


def test_video_request_forwards_params_and_upload_method():
    body = """
    console.log(JSON.stringify(buildVideoRequest({ prompt: 'run', model: 'v1', duration: 8, resolution: '720p',
      aspectRatio: '16:9', generateAudio: false, references: [{ id: 'a', role: 'last_frame' }] }, { uploadMethod: 'base64' })));
    """
    assert _run(body) == {
        "prompt": "run", "model": "v1", "upload_method": "base64", "duration": 8,
        "resolution": "720p", "aspect_ratio": "16:9", "generate_audio": False,
        "media_references": [{"id": "a", "role": "last_frame"}],
    }


def test_extend_and_edit_drop_frame_roles():
    body = """
    const refs = [{ id: 'a', role: 'reference' }, { id: 'b', role: 'first_frame' }];
    const ext = buildExtendRequest({ prompt: 'more', model: 'v1', sourceId: 'stv_1', useRealContinuation: true, concatenate: false, references: refs }, {});
    const edit = buildEditRequest({ prompt: 'bw', model: 'v2', sourceId: 'stv_2', aspectRatio: '1:1', references: refs }, {});
    console.log(JSON.stringify([ext, edit]));
    """
    ext, edit = _run(body)
    assert ext == {
        "source_video_id": "stv_1", "prompt": "more", "model": "v1", "use_real_continuation": True,
        "concatenate": False, "upload_method": "s3", "media_references": [{"id": "a", "role": "reference"}],
    }
    assert edit == {
        "source_video_id": "stv_2", "prompt": "bw", "model": "v2", "upload_method": "s3",
        "aspect_ratio": "1:1", "media_references": [{"id": "a", "role": "reference"}],
    }


def test_constraints_resolve_to_supported_values():
    body = """
    const m = { id: 'v', name: 'v', supported_resolutions: ['720p', '1080p'], supported_aspect_ratios: ['16:9', '9:16'], supported_durations: [4, 8] };
    console.log(JSON.stringify(resolveVideoParams(m, { resolution: '4k', aspectRatio: '9:16', duration: 7 })));
    """
    assert _run(body) == {"resolution": "720p", "aspectRatio": "9:16", "duration": 8}


def test_unlisted_params_resolve_to_null_not_stale_values():
    """A model without duration/resolution lists (runway/aleph-2) must not
    inherit the values chosen for the previously selected model."""
    body = """
    const m = { id: 'runway/aleph-2', name: 'Aleph', supported_resolutions: null, supported_aspect_ratios: ['16:9', '9:16'], supported_durations: null };
    console.log(JSON.stringify(resolveVideoParams(m, { resolution: '720p', aspectRatio: '9:16', duration: 8 })));
    """
    assert _run(body) == {"resolution": None, "aspectRatio": "9:16", "duration": None}


def test_tri_state_capabilities_are_preserved():
    body = """
    const c = modelCapabilities({ id: 'v', name: 'v', supports_frame_images: null, supports_character_reference: null }, 'video');
    console.log(JSON.stringify([c.firstFrame, c.lastFrame, c.refs]));
    """
    assert _run(body) == [None, None, None]


def test_character_name_match_mirrors_backend_regex():
    body = """
    console.log(JSON.stringify(characterNamesInPrompt('ANNA walks with Annabelle', [{ id: '1', name: 'anna' }, { id: '2', name: 'Bob' }])));
    """
    assert _run(body) == ["1"]


def test_validate_composer_blocks_missing_source():
    body = """
    console.log(JSON.stringify([
      validateComposer({ prompt: ' ', model: 'x', mode: 'photo' }),
      validateComposer({ prompt: 'a', model: 'v', mode: 'video', videoMode: 'extend', sourceId: null }),
      validateComposer({ prompt: 'a', model: 'v', mode: 'video', videoMode: 'generate' }),
    ]));
    """
    assert _run(body) == ["Write a prompt first.", "Choose a source video to extend.", None]
