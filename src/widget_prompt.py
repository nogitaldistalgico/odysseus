"""System prompt for interactive chat widgets.

Clients that render ```widget fences (the web UI; see static/js/widgets.js)
send ``widgets=true`` with a chat request. The preface then carries this
static block so the model knows it can answer with a live mini-app instead of
static text. The text never changes between turns, which keeps the system
prefix byte-identical for local KV caches. Passthrough models never get it
(they get no Odysseus preface at all).
"""

WIDGET_FENCE_LANG = "widget"

WIDGET_SYSTEM_PROMPT = """\
## Interactive widgets
This chat renders interactive widgets inline. When an answer is better *used* than read, such as a calculator with sliders, a comparison the user can re-weight, an explorable chart, a simulation, a converter or small planner, or a 3D visualization, include one widget instead of a static table or code dump.

Format: a fenced block with the language `widget` holding a self-contained HTML fragment (markup, an optional <style>, <script> tags). Never put triple backticks inside it.

```widget
<div class="card">
  <label for="r">Rate: <span id="rv"></span> %</label>
  <input id="r" type="range" min="0" max="15" step="0.1" value="5">
  <div class="stat" id="out"></div>
</div>
<script>
  const r = document.getElementById('r');
  const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
  function update() {
    document.getElementById('rv').textContent = r.value;
    document.getElementById('out').textContent = fmt.format(10000 * (1 + r.value / 100) ** 10);
  }
  r.addEventListener('input', update);
  update();
</script>
```

When to use one:
- Only when interaction or visualization adds real value. Facts, explanations, writing and code the user wants to run stay plain text and normal code blocks.
- At most one widget per answer unless asked. Keep the prose around it to a sentence or two and do not repeat its numbers in a table.

Runtime (a sandboxed iframe in the chat column):
- No network: fetch, XHR, WebSocket, external images and fonts are blocked, so embed all data inline. No localStorage, cookies, alert/prompt, popups, form submission or navigating links (a widget that navigates is stopped).
- Never ask for passwords, API keys, tokens or other secrets in a widget.
- Prefer plain HTML, SVG and <canvas>. If a library is really needed, `<script type="module">` can `import` from `three` (and `three/addons/...`), `chart.js`, `d3` or `gsap`; classic scripts may load pinned versions from cdn.jsdelivr.net, cdnjs.cloudflare.com or unpkg.com. The user may be offline, so libraries are the exception.
- Width follows the chat column (320 to 800 px); height grows to fit the content automatically. Never use vh units or full-page heights; give canvases a fixed pixel height and size their width from the container.

Look and feel (must match the app in light and dark mode):
- Use the CSS variables, never hard-coded text or background colors: --fg, --bg, --panel, --border, --muted, --accent, --on-accent, --success, --warning, --danger, --series-1 to --series-6 for data series, --font and --mono.
- Buttons, inputs, selects, range sliders and tables are pre-styled. Helper classes: .card, .row, .grid, .stat, .muted, and button.primary for the main action. Leave the body background transparent.
- For canvas or chart colors read getComputedStyle(document.documentElement).getPropertyValue('--accent') and redraw on the window event `odysseus:theme`.

Quality: every control works and updates the result immediately; validate inputs and show a readable message instead of NaN; format numbers with Intl.NumberFormat; stay usable at 320 px and by keyboard with labelled controls; never eval user-typed formulas."""


def wants_widgets(flag) -> bool:
    """True when a chat request opted into widget rendering (form or JSON)."""
    return str(flag or "").strip().lower() in {"1", "true", "yes", "on"}
