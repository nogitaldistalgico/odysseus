/**
 * Media Studio — settings sheet.
 *
 * Per-user UI preferences (stored via /api/prefs/studio_web_prefs, mirrored
 * in localStorage): default models, how reference images travel to
 * OpenRouter, the Magic-prompt model/system prompt and the character prompt
 * defaults. None of these change the backend's own configuration; they only
 * decide what the WebUI sends with each request.
 *
 * @module studio/prefsPanel
 */

import { CHARACTER_SUFFIX_DEFAULT, CHARACTER_TEMPLATE_DEFAULT, MAGIC_SYSTEM_PROMPT_DEFAULT } from './payload.js';

const CLOSE_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';

export function createPrefsPanel(ctx) {
  const { store, esc } = ctx;
  let el = null;
  let unregister = null;

  function options(list, current) {
    const rows = list.map(m => `<option value="${esc(m.id)}" ${m.id === current ? 'selected' : ''}>${esc(m.name)}</option>`);
    if (current && !list.some(m => m.id === current)) rows.unshift(`<option value="${esc(current)}" selected>${esc(current)}</option>`);
    return `<option value="">— first available —</option>${rows.join('')}`;
  }

  function template() {
    const p = store.prefs;
    return `
      <div class="st-sheet-card st-prefs-card">
        <div class="st-sheet-head">
          <div class="st-sheet-title">Studio settings</div>
          <span class="st-grow"></span>
          <button type="button" class="st-icon-btn st-sheet-close" aria-label="Close">${CLOSE_ICON}</button>
        </div>
        <div class="st-sheet-body st-prefs-body">
          <section class="st-prefs-section">
            <h5>Default models</h5>
            <label class="st-field"><span class="st-field-label">Photo model</span>
              <select class="st-input st-select" data-pref="photoModel">${options(store.models.photo, p.photoModel)}</select></label>
            <label class="st-field"><span class="st-field-label">Video model</span>
              <select class="st-input st-select" data-pref="videoModel">${options(store.models.video, p.videoModel)}</select></label>
            <div class="st-hint">Picking a model in the composer also updates these.</div>
          </section>

          <section class="st-prefs-section">
            <h5>Reference images for video</h5>
            <div class="st-seg st-seg-sub" data-pref-seg="uploadMethod">
              <button type="button" class="st-seg-btn${p.uploadMethod !== 'base64' ? ' active' : ''}" data-value="s3">S3 presigned URL</button>
              <button type="button" class="st-seg-btn${p.uploadMethod === 'base64' ? ' active' : ''}" data-value="base64">Inline (base64)</button>
            </div>
            <div class="st-hint">Plain reference images for <b>Generate video</b> are uploaded to your S3 bucket and handed to OpenRouter as a short-lived URL (needs <code>S3_ENDPOINT_URL</code>, <code>S3_ACCESS_KEY_ID</code>, <code>S3_SECRET_ACCESS_KEY</code>, <code>S3_BUCKET_NAME</code> in <code>.env</code>). Without S3, choose <b>Inline</b>. Note: <b>Extend</b> and <b>Edit</b> always use S3 for the source video and any reference images; first/last frame images are always inlined; photo references are always inlined.</div>
          </section>

          <section class="st-prefs-section">
            <h5>Magic prompt</h5>
            <label class="st-field"><span class="st-field-label">Model <span class="st-hint">OpenRouter chat model id · blank = server default</span></span>
              <input type="text" class="st-input" data-pref="magicModel" placeholder="e.g. anthropic/claude-sonnet-4.5" value="${esc(p.magicModel || '')}"></label>
            <label class="st-field"><span class="st-field-label">System prompt <span class="st-hint">blank = server default</span></span>
              <textarea class="st-input st-textarea" rows="4" data-pref="magicSystemPrompt" placeholder="${esc(MAGIC_SYSTEM_PROMPT_DEFAULT)}">${esc(p.magicSystemPrompt || '')}</textarea></label>
          </section>

          <section class="st-prefs-section">
            <h5>Characters</h5>
            <label class="st-field"><span class="st-field-label">Mapping template <span class="st-hint">placeholders: {pseudo} {start_idx} {end_idx}</span></span>
              <textarea class="st-input st-textarea" rows="3" data-pref="characterTemplate" placeholder="${esc(CHARACTER_TEMPLATE_DEFAULT)}">${esc(p.characterTemplate || '')}</textarea></label>
            <label class="st-field"><span class="st-field-label">Scene suffix <span class="st-hint">appended when a scene reference image precedes the character images</span></span>
              <textarea class="st-input st-textarea" rows="3" data-pref="characterSuffix" placeholder="${esc(CHARACTER_SUFFIX_DEFAULT)}">${esc(p.characterSuffix || '')}</textarea></label>
          </section>

          <section class="st-prefs-section">
            <h5>Notifications</h5>
            <label class="st-switch-row"><span>Toast when a video finishes</span><span class="st-switch"><input type="checkbox" data-pref-bool="resultToast" ${p.resultToast !== false ? 'checked' : ''}><span class="st-switch-knob"></span></span></label>
          </section>

          <section class="st-prefs-section">
            <button type="button" class="st-btn st-btn-ghost st-btn-sm" id="st-prefs-reset">Reset to defaults</button>
          </section>
        </div>
      </div>`;
  }

  function wire() {
    el.querySelector('.st-sheet-close').addEventListener('click', close);
    el.addEventListener('click', (e) => { if (e.target === el) close(); });
    el.querySelectorAll('[data-pref]').forEach(input => {
      const key = input.dataset.pref;
      const ev = input.tagName === 'SELECT' ? 'change' : 'input';
      input.addEventListener(ev, () => ctx.savePrefs({ [key]: input.value }));
    });
    el.querySelectorAll('[data-pref-bool]').forEach(input => {
      input.addEventListener('change', () => ctx.savePrefs({ [input.dataset.prefBool]: input.checked }));
    });
    el.querySelector('[data-pref-seg="uploadMethod"]').addEventListener('click', (e) => {
      const b = e.target.closest('.st-seg-btn'); if (!b) return;
      e.currentTarget.querySelectorAll('.st-seg-btn').forEach(x => x.classList.toggle('active', x === b));
      ctx.savePrefs({ uploadMethod: b.dataset.value });
    });
    el.querySelector('#st-prefs-reset').addEventListener('click', async () => {
      const ok = await ctx.confirm('Reset all Studio settings to their defaults?', { confirmText: 'Reset', danger: true });
      if (!ok) return;
      ctx.savePrefs({
        photoModel: '', videoModel: '', uploadMethod: 's3', magicModel: '', magicSystemPrompt: '',
        characterSuffix: '', characterTemplate: '', photoSize: '', resultToast: true,
      });
      rerender();
    });
  }

  function rerender() {
    if (!el) return;
    el.innerHTML = template();
    wire();
  }

  function open() {
    const host = ctx.overlayHost();
    if (!host) return;
    if (el) { rerender(); return; }
    el = document.createElement('div');
    el.className = 'st-sheet st-sheet-right';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Studio settings');
    el.innerHTML = template();
    host.appendChild(el);
    wire();
    unregister = ctx.registerMenuDismiss(close);
    if (!store.modelsLoaded) ctx.loadModels().then(rerender).catch(() => {});
  }

  function close() {
    if (!el) return;
    const node = el; el = null;
    if (unregister) { unregister(); unregister = null; }
    node.classList.add('closing');
    setTimeout(() => node.remove(), 180);
  }

  return { open, close };
}
