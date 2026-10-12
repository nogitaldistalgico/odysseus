// Interactive widgets: how a ```widget fence renders and streams, and the pure
// protocol/document helpers the browser host (widgets.js) relies on.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadMarkdown } from './markdownHarness.mjs';
import { splitFinalized, describeOpenFence } from '../../static/js/streamingSegmenter.js';
import {
  MSG,
  MAX_WIDGET_HEIGHT,
  MIN_WIDGET_HEIGHT,
  WIDGET_CSP,
  WIDGET_LANG,
  buildWidgetDocument,
  buildWidgetTheme,
  clampWidgetHeight,
  readWidgetMessage,
  safeCssValue,
  themeCss,
  themeMessage,
  widgetHash,
} from '../../static/js/widgetDocument.js';

const md = await loadMarkdown();
const WIDGET = '<div id="out"></div>\n<script>\n  out.textContent = "<b>" + (1 < 2);\n</script>';
const fence = (body) => '```widget\n' + body + '\n```';

test('a widget fence renders as a wrapped, highlighted-as-HTML code block', () => {
  const html = md.mdToHtml(`Here:\n\n${fence(WIDGET)}\n\nDone.`);
  assert.match(html, /<div class="ody-widget"><pre><code class="language-html" data-lang="widget">/);
  // The source stays escaped inside the code block: nothing executes in the app DOM.
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('class="run-code"'));
  assert.match(html, /<\/pre><\/div>/);
});

test('other fences are unchanged', () => {
  const html = md.mdToHtml('```html\n<p>x</p>\n```');
  assert.ok(!html.includes('ody-widget'));
  assert.match(html, /<pre><code class="language-html" data-lang="html">/);
});

test('rendering a widget is deterministic (required by the streaming freezer)', () => {
  const src = fence(WIDGET);
  assert.equal(md.mdToHtml(src), md.mdToHtml(src));
});

test('an open widget fence is detected for the streaming placeholder', () => {
  const open = describeOpenFence('```widget\n<div>');
  assert.equal(open.lang, WIDGET_LANG);
  assert.equal(describeOpenFence(fence(WIDGET)), null);
});

test('a closed widget fence is frozen at once, so it mounts exactly one iframe', () => {
  const text = `Intro\n\n${fence(WIDGET)}`;
  assert.equal(splitFinalized(text, md.mdToHtml), text.length);
});

test('widget document: CSP first, then theme, bridge and the widget body', () => {
  const theme = buildWidgetTheme(() => '');
  const doc = buildWidgetDocument('<!doctype html><p id="w">hi</p>', theme);
  assert.ok(doc.startsWith('<!DOCTYPE html><html data-theme="dark"'));
  const csp = doc.indexOf('Content-Security-Policy');
  assert.ok(csp > 0 && csp < doc.indexOf('<script'), 'CSP precedes every script');
  assert.ok(doc.includes(WIDGET_CSP));
  assert.ok(doc.includes('<script type="importmap">'));
  assert.ok(doc.includes('id="ody-theme"'));
  assert.ok(doc.endsWith('<body><p id="w">hi</p></body></html>'), 'model doctype is dropped');
  assert.ok(doc.includes(MSG.resize) && doc.includes(MSG.theme) && doc.includes(MSG.leave));
});

test('CSP blocks network, forms and base hijacking', () => {
  for (const d of ["default-src 'none'", "connect-src 'none'", "form-action 'none'", "base-uri 'none'"]) {
    assert.ok(WIDGET_CSP.includes(d), d);
  }
  assert.ok(!/nonce-/.test(WIDGET_CSP));
});

test('theme follows the host tokens and picks the scheme from the background', () => {
  const host = { '--bg': '#f5f5f5', '--fg': '#2b2b2b', '--panel': '#fff', '--border': '#bbb', '--red': '#ffd60a' };
  const theme = buildWidgetTheme((name) => host[name] || '');
  assert.equal(theme.scheme, 'light');
  assert.equal(theme.vars.accent, '#ffd60a');
  assert.equal(theme.vars.onAccent, '#111111', 'dark text on a bright accent');
  assert.match(themeCss(theme), /--on-accent:#111111/);
  assert.equal(buildWidgetTheme(() => '').scheme, 'dark');
  assert.equal(buildWidgetTheme((n) => (n === '--bg' ? 'rgb(20, 20, 20)' : '')).scheme, 'dark');
});

test('theme values cannot break out of the style element', () => {
  assert.equal(safeCssValue('red}</style><script>alert(1)</script>'), '');
  assert.equal(safeCssValue('#fff;background:url(x)'), '');
  assert.equal(safeCssValue('"Inter", system-ui, sans-serif'), '"Inter", system-ui, sans-serif');
  const theme = buildWidgetTheme((n) => (n === '--fg' ? '}</style><script>x()</script>' : ''));
  assert.ok(!themeCss(theme).includes('<'));
  assert.ok(!buildWidgetDocument('', theme).includes('x()'));
});

test('theme message carries css and scheme only', () => {
  const msg = themeMessage(buildWidgetTheme(() => ''));
  assert.deepEqual(Object.keys(msg).sort(), ['css', 'scheme', 'type']);
  assert.equal(msg.type, MSG.theme);
});

test('messages from a widget are validated and clamped', () => {
  assert.deepEqual(readWidgetMessage({ type: MSG.ready }), { type: MSG.ready });
  assert.deepEqual(readWidgetMessage({ type: MSG.leave, extra: 1 }), { type: MSG.leave });
  assert.deepEqual(readWidgetMessage({ type: MSG.resize, height: 312.2 }), { type: MSG.resize, height: 313 });
  assert.equal(readWidgetMessage({ type: MSG.resize, height: 1e9 }).height, MAX_WIDGET_HEIGHT);
  assert.equal(readWidgetMessage({ type: MSG.resize, height: -5 }).height, MIN_WIDGET_HEIGHT);
  assert.equal(readWidgetMessage({ type: MSG.resize, height: 'NaN' }), null);
  assert.equal(readWidgetMessage({ type: MSG.render, html: 'x' }), null, 'frames cannot send host messages');
  assert.equal(readWidgetMessage('ody-widget:ready'), null);
  assert.equal(readWidgetMessage(null), null);
  assert.equal(readWidgetMessage({ type: MSG.error, message: 'x'.repeat(1000) }).message.length, 300);
  assert.equal(clampWidgetHeight(Infinity), null);
});

test('widget hash is stable and source-sensitive', () => {
  assert.equal(widgetHash(WIDGET), widgetHash(WIDGET));
  assert.notEqual(widgetHash(WIDGET), widgetHash(WIDGET + ' '));
});
