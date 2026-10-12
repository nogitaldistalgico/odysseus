// widgets.js
//
// DOM side of interactive chat widgets. markdown.js renders a ```widget fence
// as an ordinary (highlighted, copyable) code block wrapped in
// <div class="ody-widget">. This module upgrades such blocks inside the chat
// history into sandboxed iframes; anywhere else, or when JS fails, the code
// block simply stays visible. The pure half (document, theme, protocol) lives
// in widgetDocument.js.
//
// Lifecycle of one widget:
//   1. A MutationObserver sees a new .ody-widget in #chat-history and mounts
//      <iframe sandbox="allow-scripts" src=WIDGET_FRAME_URL>.
//   2. The frame posts `ready`; we answer with `render` (the full document).
//   3. The widget posts `resize` whenever its height changes; we size the
//      iframe and remember the height by source hash, so the re-render chat.js
//      does when a stream finishes re-mounts at the right size.
//   4. Theme changes are pushed to every live widget.
// Messages are only accepted from an iframe we mounted (event.source) with the
// opaque origin a sandboxed frame reports ('null').

import {
  WIDGET_FRAME_URL,
  DEFAULT_WIDGET_HEIGHT,
  MSG,
  buildWidgetDocument,
  buildWidgetTheme,
  readWidgetMessage,
  themeMessage,
  widgetHash,
} from './widgetDocument.js';

const HOST_SELECTOR = '#chat-history';
const HEIGHT_CACHE_LIMIT = 200;
const SETTLE_MS = 250;

const live = new Set(); // { box, stage, iframe, src, hash, rendered }
const heightCache = new Map(); // source hash -> last reported height

const ICON_CODE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
const ICON_RELOAD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';

function currentTheme() {
  const cs = getComputedStyle(document.documentElement);
  return buildWidgetTheme((name) => cs.getPropertyValue(name));
}

function rememberHeight(hash, height) {
  heightCache.delete(hash);
  heightCache.set(hash, height);
  if (heightCache.size > HEIGHT_CACHE_LIMIT) {
    heightCache.delete(heightCache.keys().next().value);
  }
}

function widgetSource(box) {
  // The fallback code block holds the exact fence body; hljs only wraps it in
  // spans, so textContent is the source (including in-place edits).
  return box.querySelector(':scope > pre > code')?.textContent || '';
}

function mountFrame(rec) {
  const iframe = document.createElement('iframe');
  iframe.className = 'ody-widget-iframe';
  iframe.setAttribute('sandbox', 'allow-scripts');
  iframe.setAttribute('allow', ''); // no camera, mic, clipboard, fullscreen, ...
  iframe.setAttribute('referrerpolicy', 'no-referrer');
  iframe.setAttribute('loading', 'lazy');
  iframe.setAttribute('title', 'Interactive widget');
  iframe.style.height = `${heightCache.get(rec.hash) || DEFAULT_WIDGET_HEIGHT}px`;
  iframe.src = WIDGET_FRAME_URL;
  // A sandboxed frame may still navigate itself (location = 'https://…'), which
  // CSP can't block. A widget never needs to, so tear it down when it does,
  // before it can pose as app UI and collect input. Expected loads: the frame
  // page (which only then sends `ready`) and the rendered document. The bridge
  // also reports `leave` on pagehide; either signal stops the widget.
  iframe.addEventListener('load', () => {
    if (rec.iframe !== iframe) return;
    rec.loads += 1;
    if (rec.loads > 2) stopNavigatedWidget(rec);
  });
  rec.iframe = iframe;
  rec.loads = 0;
  rec.rendered = false;
  rec.box.classList.remove('has-error');
  rec.box.classList.toggle('is-ready', heightCache.has(rec.hash));
  rec.stage.replaceChildren(iframe);
  live.add(rec);
}

function stopNavigatedWidget(rec) {
  live.delete(rec);
  rec.iframe.remove();
  rec.error.textContent = 'Widget stopped: it tried to navigate away. Reload to run it again.';
  rec.box.classList.add('has-error');
}

function buildToolbar(rec) {
  const bar = document.createElement('div');
  bar.className = 'ody-widget-toolbar';

  const codeBtn = document.createElement('button');
  codeBtn.type = 'button';
  codeBtn.className = 'ody-widget-btn';
  codeBtn.title = 'Show code';
  codeBtn.setAttribute('aria-pressed', 'false');
  codeBtn.innerHTML = ICON_CODE;
  codeBtn.addEventListener('click', () => {
    const on = rec.box.classList.toggle('show-source');
    codeBtn.setAttribute('aria-pressed', String(on));
    codeBtn.title = on ? 'Show widget' : 'Show code';
  });

  const reloadBtn = document.createElement('button');
  reloadBtn.type = 'button';
  reloadBtn.className = 'ody-widget-btn';
  reloadBtn.title = 'Reload widget';
  reloadBtn.innerHTML = ICON_RELOAD;
  reloadBtn.addEventListener('click', () => {
    rec.src = widgetSource(rec.box);
    rec.box.classList.remove('show-source');
    codeBtn.setAttribute('aria-pressed', 'false');
    mountFrame(rec);
  });

  bar.append(codeBtn, reloadBtn);
  return bar;
}

function hydrate(box) {
  if (box.dataset.widgetState || !box.closest(HOST_SELECTOR)) return;
  const src = widgetSource(box);
  if (!src.trim()) {
    box.dataset.widgetState = 'static'; // nothing to run: keep the code block
    return;
  }
  box.dataset.widgetState = 'live';
  const stage = document.createElement('div');
  stage.className = 'ody-widget-stage';
  const error = document.createElement('div');
  error.className = 'ody-widget-error';
  const rec = { box, stage, error, iframe: null, src, hash: widgetHash(src), rendered: false };
  box.prepend(stage, buildToolbar(rec), error);
  box.classList.add('is-live');
  mountFrame(rec);
}

// Some renderers (group chat, the full-render fallback) rewrite a message's
// innerHTML on every token, replacing the widget block each time. Mount only
// once a block has stayed in the DOM for a moment, so a streaming reply
// doesn't reload its iframe per token; until then CSS shows a placeholder.
function queueHydrate(box) {
  if (box.dataset.widgetState || box.dataset.widgetQueued) return;
  box.dataset.widgetQueued = '1';
  setTimeout(() => {
    delete box.dataset.widgetQueued;
    if (box.isConnected) hydrate(box);
  }, SETTLE_MS);
}

function scan(root) {
  if (!root || root.nodeType !== 1) return;
  if (root.matches('.ody-widget')) queueHydrate(root);
  else root.querySelectorAll('.ody-widget').forEach(queueHydrate);
}

function recordFor(source) {
  for (const rec of live) {
    if (!rec.iframe.isConnected) {
      live.delete(rec);
      continue;
    }
    if (rec.iframe.contentWindow === source) return rec;
  }
  return null;
}

function onMessage(e) {
  const msg = readWidgetMessage(e.data);
  if (!msg || e.origin !== 'null') return;
  const rec = recordFor(e.source);
  if (!rec) return;
  if (msg.type === MSG.ready) {
    if (rec.rendered) return;
    rec.rendered = true;
    e.source.postMessage({ type: MSG.render, html: buildWidgetDocument(rec.src, currentTheme()) }, '*');
  } else if (msg.type === MSG.resize) {
    rec.iframe.style.height = `${msg.height}px`;
    rememberHeight(rec.hash, msg.height);
    rec.box.classList.add('is-ready');
  } else if (msg.type === MSG.leave) {
    stopNavigatedWidget(rec);
  } else if (msg.type === MSG.error) {
    rec.error.textContent = `Widget error: ${msg.message}`;
    rec.box.classList.add('has-error');
  }
}

let lastThemeKey = '';
let themeQueued = false;

function pushTheme() {
  themeQueued = false;
  if (!live.size) return;
  const theme = currentTheme();
  const key = JSON.stringify(theme);
  if (key === lastThemeKey) return;
  lastThemeKey = key;
  const message = themeMessage(theme);
  for (const rec of live) {
    if (!rec.iframe.isConnected) {
      live.delete(rec);
      continue;
    }
    // A frame that hasn't rendered yet gets the current theme with `render`.
    if (rec.rendered) rec.iframe.contentWindow?.postMessage(message, '*');
  }
}

function scheduleThemePush() {
  if (themeQueued) return;
  themeQueued = true;
  requestAnimationFrame(pushTheme);
}

(function wireWidgets() {
  if (window._odyWidgetsWired) return;
  window._odyWidgetsWired = true;
  window.addEventListener('message', onMessage);
  const start = () => {
    if (!document.body) return;
    scan(document.body);
    new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) scan(node);
      }
    }).observe(document.body, { childList: true, subtree: true });
    // theme.js writes tokens as inline styles on <html> and toggles .light.
    new MutationObserver(scheduleThemePush)
      .observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class'] });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
