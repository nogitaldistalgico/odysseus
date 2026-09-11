"""Pin the Media Studio WebUI shell wiring.

The Studio window is reachable from the sidebar, the icon rail, the /studio
deep link and the keyboard "toggle window" shortcut, and it participates in the
minimise dock and the Customize-UI visibility switches. Each of those is a
hand-maintained table in a different file; this test keeps them in step so a
refactor of one cannot silently orphan the tool.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def test_index_html_has_sidebar_and_rail_launchers():
    html = _read("static/index.html")
    assert 'id="tool-studio-btn"' in html
    assert 'id="rail-studio"' in html
    assert 'id="studio-status-dot"' in html
    assert '<link rel="stylesheet" href="/static/css/studio.css">' in html
    assert 'data-ui-key="tool-studio"' in html
    assert "'/studio': 'Studio — Odysseus'" in html


def test_app_js_wires_button_route_and_rail():
    app_js = _read("static/app.js")
    assert "el('tool-studio-btn')" in app_js
    assert "import('./js/studio/studio.js')" in app_js
    assert "'/studio':" in app_js
    assert "'rail-studio':    'tool-studio-btn'" in app_js


def test_modal_manager_knows_the_studio_window():
    mm = _read("static/js/modalManager.js")
    assert re.search(r"'studio-modal':\s*\{ rail: 'rail-studio',\s*sidebar: 'tool-studio-btn' \}", mm)
    assert "'studio-modal':      { label: 'Studio'" in mm
    # A swipe-down must dock (state preserved), not tear the composer down.
    assert re.search(r"_SWIPE_DOWN_MINIMIZES = new Set\(\[[^\]]*'studio-modal'", mm, re.S)


def test_visibility_shortcut_and_privilege_gates():
    assert "'tool-studio':         '#tool-studio-btn, #rail-studio'" in _read("static/js/ui_visibility.js")
    assert "'studio-modal':           'tool-studio-btn'" in _read("static/js/keyboard-shortcuts.js")
    assert "hideOn('#tool-studio-btn, #rail-studio', privs.can_generate_images)" in _read("static/js/init.js")


def test_deep_link_route_serves_the_spa():
    app_py = _read("app.py")
    assert re.search(r'@app\.get\("/studio"\)\s*\nasync def serve_studio\(request: Request\):\s*\n\s*return await serve_index\(request\)', app_py)


def test_studio_modules_exist():
    for name in ("studio", "api", "payload", "composer", "library", "characters", "picker", "prefsPanel"):
        assert (_REPO / "static" / "js" / "studio" / f"{name}.js").exists(), name
    assert (_REPO / "static" / "css" / "studio.css").exists()
