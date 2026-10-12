// widgetDocument.js
//
// Pure (DOM-free) half of interactive chat widgets: the document a widget runs
// in, the theme it receives, and the postMessage protocol between the chat and
// the widget frame. widgets.js owns the DOM side.
//
// A widget is a ```widget fence whose body is a self-contained HTML fragment.
// It runs in <iframe sandbox="allow-scripts" src="/static/widget-frame.html">:
// an opaque origin (no cookies, storage or DOM access to the app) whose own CSP
// header (core/middleware.py) limits scripts to inline code plus a few CDNs and
// blocks all network access besides them. The frame page waits for one
// `render` message from its parent and document.write()s the string built here.
// srcdoc is not an option: it inherits the app's nonce CSP, which blocks the
// inline scripts and handlers every widget needs.

export const WIDGET_LANG = 'widget';
export const WIDGET_FRAME_URL = '/static/widget-frame.html';
export const MIN_WIDGET_HEIGHT = 40;
export const MAX_WIDGET_HEIGHT = 2400;
export const DEFAULT_WIDGET_HEIGHT = 180;

// Message types. Frame → host: ready, resize, error, leave. Host → frame:
// render, theme.
export const MSG = {
  ready: 'ody-widget:ready',
  resize: 'ody-widget:resize',
  error: 'ody-widget:error',
  leave: 'ody-widget:leave',
  render: 'ody-widget:render',
  theme: 'ody-widget:theme',
};

// Pinned ES modules for `import ... from 'three'` etc. inside a widget.
export const WIDGET_IMPORT_MAP = {
  imports: {
    three: 'https://esm.sh/three@0.170.0',
    'three/addons/': 'https://esm.sh/three@0.170.0/examples/jsm/',
    'chart.js': 'https://esm.sh/chart.js@4.4.7/auto',
    d3: 'https://esm.sh/d3@7.9.0',
    gsap: 'https://esm.sh/gsap@3.12.5',
  },
};

// Kept in sync with the header in core/middleware.py (WIDGET_FRAME_CSP). The
// meta copy is defense in depth: a second policy can only tighten the first.
export const WIDGET_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' 'unsafe-eval' https://esm.sh https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  'media-src data: blob:',
  'worker-src blob:',
  "connect-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join('; ');

// Host token → widget token. Widgets see a small, stable vocabulary instead of
// the app's internal variable names.
const THEME_TOKENS = {
  bg: '--bg',
  fg: '--fg',
  panel: '--panel',
  border: '--border',
  accent: '--red',
  success: '--color-success',
  warning: '--color-warning',
  danger: '--color-danger',
};

const FALLBACK_THEME = {
  bg: '#282c34',
  fg: '#9cdef2',
  panel: '#111111',
  border: '#355a66',
  accent: '#e06c75',
  success: '#4caf50',
  warning: '#f0ad4e',
  danger: '#c0392b',
};

// Theme values end up inside a <style> block, so only accept plain color/font
// syntax — nothing that could close the rule or the element.
const SAFE_CSS_VALUE = /^[\w\s#%().,'"+-]{1,120}$/;

export function safeCssValue(value) {
  const v = String(value ?? '').trim();
  return SAFE_CSS_VALUE.test(v) ? v : '';
}

function parseColor(value) {
  const v = String(value || '').trim().toLowerCase();
  let m = v.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/);
  if (m) {
    const hex = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1];
    return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  }
  m = v.match(/^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  return null;
}

// Relative luminance (WCAG); null for colors we can't parse.
export function luminance(value) {
  const rgb = parseColor(value);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Build the widget theme from the host's CSS custom properties.
 * `lookup(name)` returns the computed value of a host variable ('' if unset).
 */
export function buildWidgetTheme(lookup) {
  const vars = {};
  for (const [key, hostVar] of Object.entries(THEME_TOKENS)) {
    vars[key] = safeCssValue(lookup(hostVar)) || FALLBACK_THEME[key];
  }
  const font = safeCssValue(lookup('--font-family'));
  if (font) vars.font = font;
  const accentLum = luminance(vars.accent);
  vars.onAccent = accentLum !== null && accentLum > 0.45 ? '#111111' : '#ffffff';
  const bgLum = luminance(vars.bg);
  const scheme = bgLum !== null && bgLum > 0.4 ? 'light' : 'dark';
  return { scheme, vars };
}

export function themeCss(theme) {
  const vars = (theme && theme.vars) || FALLBACK_THEME;
  const decls = [];
  const names = { onAccent: 'on-accent' };
  for (const [key, value] of Object.entries(vars)) {
    const safe = safeCssValue(value);
    if (safe) decls.push(`--${names[key] || key}:${safe}`);
  }
  return `:root{${decls.join(';')}}`;
}

// Short stable hash of the widget source (FNV-1a), used to remember heights
// across re-renders so a re-mounted widget doesn't jump.
export function widgetHash(src) {
  let h = 0x811c9dc5;
  const s = String(src || '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}

export function clampWidgetHeight(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.min(MAX_WIDGET_HEIGHT, Math.max(MIN_WIDGET_HEIGHT, Math.ceil(n)));
}

/**
 * Validate a message coming out of a widget frame. Returns a normalized
 * message or null. The caller still has to check `event.source`.
 */
export function readWidgetMessage(data) {
  if (!data || typeof data !== 'object') return null;
  if (data.type === MSG.ready) return { type: MSG.ready };
  if (data.type === MSG.resize) {
    const height = clampWidgetHeight(data.height);
    return height === null ? null : { type: MSG.resize, height };
  }
  if (data.type === MSG.leave) return { type: MSG.leave };
  if (data.type === MSG.error) {
    return { type: MSG.error, message: String(data.message || 'Unknown error').slice(0, 300) };
  }
  return null;
}

// Odysseus × Apple base styles. Widgets get good-looking controls for free and
// only add layout-specific CSS. html/body heights are pinned to their content
// (full-page documents often set height:100% or 100vh), otherwise the measured
// height would just echo the iframe's and the widget could never grow.
const BASE_CSS = `
:root{--radius:12px;--radius-sm:8px;--gap:12px;
--font:-apple-system,BlinkMacSystemFont,"SF Pro Text","Inter","Segoe UI",system-ui,sans-serif;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--muted:color-mix(in srgb,var(--fg) 62%,var(--bg));
--series-1:#0a84ff;--series-2:#30d158;--series-3:#ff9f0a;--series-4:#bf5af2;--series-5:#ff375f;--series-6:#64d2ff}
:root[data-theme=light]{--series-1:#007aff;--series-2:#34c759;--series-3:#ff9500;--series-4:#af52de;--series-5:#ff2d55;--series-6:#32ade6}
*,*::before,*::after{box-sizing:border-box}
html{overflow:hidden;background:transparent}
html,body{height:auto!important;min-height:0!important}
html[data-overflow]{overflow-y:auto}
body{margin:0;padding:2px;display:flow-root;background:transparent;color:var(--fg);
font:14px/1.5 var(--font);-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{margin:0 0 .5em;line-height:1.25;letter-spacing:-.01em}
h1{font-size:20px}h2{font-size:17px}h3{font-size:15px}
p{margin:0 0 .75em}
button,input,select,textarea{font:inherit;color:var(--fg)}
button{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);
padding:6px 14px;cursor:pointer;transition:border-color .15s,background .15s,transform .1s}
button:hover{border-color:var(--accent)}
button:active{transform:scale(.97)}
button.primary{background:var(--accent);border-color:transparent;color:var(--on-accent)}
button:disabled{opacity:.5;cursor:default}
input,select,textarea{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px 10px}
input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible{
outline:2px solid color-mix(in srgb,var(--accent) 55%,transparent);outline-offset:1px}
input[type=range]{width:100%;padding:0;border:0;background:none;accent-color:var(--accent)}
input[type=checkbox],input[type=radio]{accent-color:var(--accent)}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left}
th{font-size:12px;font-weight:600;color:var(--muted)}
code,pre{font-family:var(--mono);font-size:12.5px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:14px}
.row{display:flex;flex-wrap:wrap;gap:var(--gap);align-items:center}
.grid{display:grid;gap:var(--gap);grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
.muted{color:var(--muted)}
.stat{font-size:26px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
canvas,svg,img{max-width:100%}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
`;

// Runs inside the widget before any widget code: height reporting, error
// reporting, live theme updates and a `leave` notice if the widget navigates
// away. `parent` is captured first because widget code may reassign it.
// Feedback guard: content sized from the viewport (100vh, height:100% on a
// wrapper) grows by exactly as much as the frame just grew; reporting that
// would resize the frame forever, so such a measurement is not reported. Posts carry only a height or an error
// string, so the '*' target (the parent is the app, the frame is opaque) leaks
// nothing.
function bridgeScript() {
  return `(function(){
var MAX=${MAX_WIDGET_HEIGHT},P=parent,root=document.documentElement,last=-1,queued=false,lastInner=-1,lastExtra=NaN;
function post(m){try{P.postMessage(m,'*')}catch(_){}}
addEventListener('pagehide',function(){post({type:'${MSG.leave}'})});
function measure(){queued=false;var h=Math.ceil(root.getBoundingClientRect().height),inner=innerHeight,extra=h-inner;
var coupled=lastInner>=0&&inner!==lastInner&&extra===lastExtra;lastInner=inner;lastExtra=extra;
if(coupled)return;
if(h>MAX){root.setAttribute('data-overflow','')}else{root.removeAttribute('data-overflow')}
if(h!==last){last=h;post({type:'${MSG.resize}',height:Math.min(h,MAX)})}}
function schedule(){if(!queued){queued=true;requestAnimationFrame(measure)}}
var ro=new ResizeObserver(schedule);ro.observe(root);
document.addEventListener('DOMContentLoaded',function(){if(document.body)ro.observe(document.body);schedule()});
addEventListener('load',schedule);
addEventListener('error',function(e){post({type:'${MSG.error}',message:String(e.message||'Script error')})});
addEventListener('unhandledrejection',function(e){var r=e.reason;post({type:'${MSG.error}',message:String(r&&r.message||r||'Unhandled rejection')})});
addEventListener('message',function(e){if(e.source!==P)return;var d=e.data;
if(!d||d.type!=='${MSG.theme}'||typeof d.css!=='string')return;
var s=document.getElementById('ody-theme');if(s)s.textContent=d.css;
root.setAttribute('data-theme',d.scheme==='light'?'light':'dark');
root.style.colorScheme=d.scheme==='light'?'light':'dark';
dispatchEvent(new CustomEvent('odysseus:theme',{detail:{scheme:d.scheme}}))});
})();`;
}

/**
 * Build the complete document a widget runs in. `src` is the fence body;
 * `theme` comes from buildWidgetTheme().
 */
export function buildWidgetDocument(src, theme) {
  const scheme = theme && theme.scheme === 'light' ? 'light' : 'dark';
  const body = String(src || '').replace(/^\s*<!doctype[^>]*>/i, '');
  return '<!DOCTYPE html>'
    + `<html data-theme="${scheme}" style="color-scheme:${scheme}"><head>`
    + '<meta charset="utf-8">'
    + `<meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">`
    + '<meta name="viewport" content="width=device-width,initial-scale=1">'
    + `<script type="importmap">${JSON.stringify(WIDGET_IMPORT_MAP)}</script>`
    + `<style>${BASE_CSS}</style>`
    + `<style id="ody-theme">${themeCss(theme)}</style>`
    + `<script>${bridgeScript()}</script>`
    + '</head><body>'
    + body
    + '</body></html>';
}

/** Payload for a live theme update (host → frame). */
export function themeMessage(theme) {
  return { type: MSG.theme, scheme: theme.scheme, css: themeCss(theme) };
}
