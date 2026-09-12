/**
 * Media Studio — Create pane.
 *
 * Left: the composer (mode, prompt, model, model-specific parameters,
 * reference images, characters). Right: the stage, which shows the selected
 * result large plus a filmstrip of everything generated in this session.
 *
 * All request shapes come from payload.js; this module only maps UI state
 * onto them and renders what the backend reports back.
 *
 * @module studio/composer
 */

import {
  PHOTO_SIZE_PRESETS, CHARACTER_SUFFIX_DEFAULT, CHARACTER_TEMPLATE_DEFAULT,
  modelCapabilities, capabilityBadges, findModel, resolveVideoParams, ratioBox,
  buildPhotoRequest, buildVideoRequest, buildExtendRequest, buildEditRequest,
  buildCostRequest, buildMagicRequest, referenceWarnings, validateComposer,
  characterNamesInPrompt, formatUsd, photoPriceLabel, formatDuration, formatBytes,
  relTime, errorMessage, generationModeLabel, parseServerDate,
} from './payload.js';
import { openMediaPicker } from './picker.js';
import { topPortalZ } from '../toolWindowZOrder.js';

const ICONS = {
  wand: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 4 1.5 3L20 8.5 17 10l-1.5 3L14 10l-3-1.5L14 7z"/><path d="M4 20 13 11"/><path d="M5 7l.7 1.4L7 9l-1.3.6L5 11l-.7-1.4L3 9l1.3-.6z"/></svg>',
  plus: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  close: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  chevron: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
  check: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
  dice: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.2" fill="currentColor"/><circle cx="15.5" cy="15.5" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="15.5" cy="8.5" r="1.2" fill="currentColor"/><circle cx="8.5" cy="15.5" r="1.2" fill="currentColor"/></svg>',
  heart: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.42 4.58a5.4 5.4 0 0 0-7.65 0l-.77.78-.77-.78a5.4 5.4 0 0 0-7.65 0C1.46 6.7 1.33 10.28 4 13l8 8 8-8c2.67-2.72 2.54-6.3.42-8.42z"/></svg>',
  download: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>',
  trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  film: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>',
  image: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
  library: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
  extend: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h12"/><path d="m12 8 4 4-4 4"/><path d="M20 5v14"/></svg>',
  edit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
  reuse: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
  spark: '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 17l.7 1.8 1.8.7-1.8.7L19 22l-.7-1.8-1.8-.7 1.8-.7z"/><path d="M5 3l.5 1.3L7 5l-1.5.5L5 7l-.5-1.5L3 5l1.5-.7z"/></svg>',
  upload: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/></svg>',
  warn: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
};

const ROLE_LABELS = { reference: 'Reference', first_frame: 'First frame', last_frame: 'Last frame' };

function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function createComposer(root, ctx) {
  const { api, store, bus, esc } = ctx;

  const state = {
    mode: 'photo',
    videoMode: 'generate',
    prompt: '',
    negativePrompt: '',
    photoModel: '',
    videoModel: '',
    size: '',
    seed: '',
    steps: '',
    resolution: null,
    aspectRatio: null,
    duration: null,
    generateAudio: null,
    /** Edit mode only; null = keep the source video's aspect ratio. */
    editAspectRatio: null,
    source: null,
    useRealContinuation: false,
    concatenate: true,
    references: [],
    characterIds: [],
    magicUseContext: true,
    busy: false,
    activeId: null,
    showAdvanced: false,
  };

  /** Stage-only items: photo placeholders while a sync request runs, and
   *  failed submissions the library never learned about. */
  const local = new Map();
  let costCache = new Map();
  let costTimer = null;
  let elapsedTimer = null;

  // ── Skeleton ─────────────────────────────────────────────────────────
  root.innerHTML = `
    <div class="st-create">
      <div class="st-composer" id="st-composer">
        <div class="st-composer-scroll">
          <div class="st-seg st-seg-lg" id="st-c-mode" role="radiogroup" aria-label="Media type">
            <button type="button" class="st-seg-btn active" data-value="photo" role="radio" aria-checked="true">${ICONS.image}<span>Photo</span></button>
            <button type="button" class="st-seg-btn" data-value="video" role="radio" aria-checked="false">${ICONS.film}<span>Video</span></button>
          </div>
          <div class="st-seg st-seg-sub" id="st-c-vmode" role="radiogroup" aria-label="Video operation" hidden>
            <button type="button" class="st-seg-btn active" data-value="generate" role="radio" aria-checked="true">Generate</button>
            <button type="button" class="st-seg-btn" data-value="extend" role="radio" aria-checked="false">Extend</button>
            <button type="button" class="st-seg-btn" data-value="edit" role="radio" aria-checked="false">Edit</button>
          </div>

          <section class="st-card" id="st-c-source-card" hidden>
            <div class="st-card-head"><span class="st-card-title">Source video</span></div>
            <div id="st-c-source"></div>
          </section>

          <section class="st-card st-card-prompt">
            <div class="st-card-head">
              <span class="st-card-title">Prompt</span>
              <label class="st-inline-check" id="st-c-magic-ctx-wrap" hidden title="Send the first reference image (or the source video's last frame) along with the prompt">
                <input type="checkbox" id="st-c-magic-ctx" checked> <span>use image context</span>
              </label>
              <button type="button" class="st-btn st-btn-ghost st-btn-sm" id="st-c-magic" title="Expand the prompt with an LLM (Magic prompt)">${ICONS.wand}<span>Magic</span></button>
            </div>
            <textarea id="st-c-prompt" class="st-prompt" rows="4" placeholder="Describe what you want to see…" spellcheck="true"></textarea>
            <div class="st-prompt-foot">
              <span class="st-hint" id="st-c-prompt-hint">⌘/Ctrl + Enter to generate</span>
              <button type="button" class="st-link" id="st-c-neg-toggle">Negative prompt</button>
            </div>
            <textarea id="st-c-neg" class="st-prompt st-prompt-neg" rows="2" placeholder="What to avoid (negative prompt)…" hidden></textarea>
          </section>

          <section class="st-card">
            <div class="st-card-head"><span class="st-card-title">Model</span><span class="st-card-meta" id="st-c-model-meta"></span></div>
            <button type="button" class="st-model-btn" id="st-c-model" aria-haspopup="listbox">
              <span class="st-model-name">Loading models…</span>
              <span class="st-model-badges"></span>
              ${ICONS.chevron}
            </button>
            <div class="st-notice" id="st-c-model-notice" hidden></div>
          </section>

          <section class="st-card" id="st-c-params-card">
            <div class="st-card-head"><span class="st-card-title">Settings</span></div>
            <div id="st-c-params"></div>
          </section>

          <section class="st-card" id="st-c-refs-card">
            <div class="st-card-head">
              <span class="st-card-title">Reference images</span>
              <span class="st-card-meta" id="st-c-refs-meta"></span>
              <button type="button" class="st-btn st-btn-ghost st-btn-sm" id="st-c-refs-add">${ICONS.plus}<span>Add</span></button>
            </div>
            <div class="st-refs" id="st-c-refs"></div>
            <input type="file" id="st-c-refs-file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden>
          </section>

          <section class="st-card" id="st-c-chars-card">
            <div class="st-card-head">
              <span class="st-card-title">Characters</span>
              <button type="button" class="st-link" id="st-c-chars-adv">Advanced</button>
            </div>
            <div class="st-chars" id="st-c-chars"></div>
            <div class="st-chars-adv" id="st-c-chars-advbox" hidden>
              <label class="st-field">
                <span class="st-field-label">Mapping template <span class="st-hint">{pseudo} {start_idx} {end_idx}</span></span>
                <textarea id="st-c-char-template" class="st-input st-textarea" rows="2" placeholder="${esc(CHARACTER_TEMPLATE_DEFAULT)}"></textarea>
              </label>
              <label class="st-field">
                <span class="st-field-label">Scene suffix <span class="st-hint">added when a scene reference exists</span></span>
                <textarea id="st-c-char-suffix" class="st-input st-textarea" rows="2" placeholder="${esc(CHARACTER_SUFFIX_DEFAULT)}"></textarea>
              </label>
            </div>
          </section>

          <div class="st-warnings" id="st-c-warnings"></div>
        </div>

        <div class="st-composer-foot">
          <div class="st-cost" id="st-c-cost"></div>
          <div class="st-foot-hint" id="st-c-problem"></div>
          <button type="button" class="st-btn st-btn-primary st-generate" id="st-c-generate">
            <span class="st-generate-label">Generate</span>
          </button>
        </div>
      </div>

      <div class="st-stage" id="st-stage">
        <div class="st-stage-main" id="st-stage-main"></div>
        <div class="st-stage-strip" id="st-stage-strip" hidden></div>
      </div>
    </div>`;

  const q = (sel) => root.querySelector(sel);
  const promptEl = q('#st-c-prompt');
  const negEl = q('#st-c-neg');

  // ── Derived state helpers ────────────────────────────────────────────
  const currentModelId = () => (state.mode === 'photo' ? state.photoModel : state.videoModel);
  const currentModel = () => findModel(store.models[state.mode], currentModelId());
  const caps = () => modelCapabilities(currentModel(), state.mode);

  /** Snapshot in the shape payload.js expects. */
  function current() {
    return {
      mode: state.mode,
      videoMode: state.videoMode,
      prompt: state.prompt,
      negativePrompt: state.negativePrompt,
      model: currentModelId(),
      size: state.size,
      seed: state.seed,
      steps: state.steps,
      resolution: state.resolution,
      aspectRatio: state.videoMode === 'edit' ? state.editAspectRatio : state.aspectRatio,
      duration: state.duration,
      generateAudio: state.generateAudio,
      sourceId: state.source?.id || null,
      useRealContinuation: state.useRealContinuation,
      concatenate: state.concatenate,
      references: state.references,
      characterIds: state.characterIds,
      characterSuffix: store.prefs.characterSuffix,
      characterTemplate: store.prefs.characterTemplate,
    };
  }

  function selectedCharacters() {
    return store.characters.filter(c => state.characterIds.includes(c.id));
  }

  function characterImageCount() {
    return selectedCharacters().reduce((n, c) => n + (c.images?.length || 0), 0);
  }

  // ── Mode ─────────────────────────────────────────────────────────────
  function setMode(mode) {
    if (mode !== 'photo' && mode !== 'video') return;
    state.mode = mode;
    q('#st-c-mode').querySelectorAll('.st-seg-btn').forEach(b => {
      const on = b.dataset.value === mode;
      b.classList.toggle('active', on); b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    q('#st-c-vmode').hidden = mode !== 'video';
    ensureModelDefaults();
    renderAll();
  }

  function setVideoMode(vm) {
    if (!['generate', 'extend', 'edit'].includes(vm)) return;
    state.videoMode = vm;
    q('#st-c-vmode').querySelectorAll('.st-seg-btn').forEach(b => {
      const on = b.dataset.value === vm;
      b.classList.toggle('active', on); b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    if (vm === 'edit') {
      const c = caps();
      if (c && !c.editing) {
        const alt = store.models.video.find(m => m.supports_video_editing);
        if (alt) { state.videoModel = alt.id; ctx.savePrefs({ videoModel: alt.id }); ctx.toast(`Switched to ${alt.name} (video editing)`); }
      }
    }
    if (vm === 'extend' && state.useRealContinuation) {
      const c = caps();
      if (c && !c.continuation) state.useRealContinuation = false;
    }
    renderAll();
  }

  q('#st-c-mode').addEventListener('click', (e) => {
    const b = e.target.closest('.st-seg-btn'); if (b) setMode(b.dataset.value);
  });
  q('#st-c-vmode').addEventListener('click', (e) => {
    const b = e.target.closest('.st-seg-btn'); if (b) setVideoMode(b.dataset.value);
  });

  // ── Prompt ───────────────────────────────────────────────────────────
  const autoGrow = (ta) => { ta.style.height = 'auto'; ta.style.height = Math.min(320, ta.scrollHeight + 2) + 'px'; };
  promptEl.addEventListener('input', () => { state.prompt = promptEl.value; autoGrow(promptEl); renderWarnings(); renderFooter(); });
  promptEl.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
  });
  promptEl.addEventListener('paste', (e) => {
    const files = Array.from(e.clipboardData?.files || []).filter(f => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  negEl.addEventListener('input', () => { state.negativePrompt = negEl.value; autoGrow(negEl); });
  q('#st-c-neg-toggle').addEventListener('click', () => {
    negEl.hidden = !negEl.hidden;
    if (!negEl.hidden) negEl.focus();
  });

  // Magic prompt ---------------------------------------------------------
  function magicContextId() {
    if (state.mode === 'video' && state.videoMode !== 'generate') return state.source?.id || null;
    const first = state.references.find(r => r.id && !r.uploading);
    return first ? first.id : null;
  }
  q('#st-c-magic-ctx').addEventListener('change', (e) => { state.magicUseContext = e.target.checked; });
  q('#st-c-magic').addEventListener('click', async () => {
    const text = promptEl.value.trim();
    if (!text) { ctx.toast('Write a short prompt first — Magic expands it.'); promptEl.focus(); return; }
    const btn = q('#st-c-magic');
    btn.disabled = true; btn.classList.add('st-busy');
    const previous = promptEl.value;
    try {
      const mediaId = state.magicUseContext ? magicContextId() : null;
      const res = await api.magicPrompt(buildMagicRequest(text, mediaId, store.prefs));
      const enhanced = String(res?.enhanced_prompt || '').trim();
      if (!enhanced) throw new Error('Magic prompt returned nothing');
      promptEl.value = enhanced; state.prompt = enhanced; autoGrow(promptEl);
      renderWarnings(); renderFooter();
      ctx.toast('Prompt enhanced', {
        leadingIcon: 'check', duration: 6000, action: 'Undo',
        onAction: () => { promptEl.value = previous; state.prompt = previous; autoGrow(promptEl); renderWarnings(); },
      });
    } catch (err) {
      ctx.reportError(err, 'Magic prompt failed');
    } finally {
      btn.disabled = false; btn.classList.remove('st-busy');
    }
  });

  // ── Model picker ─────────────────────────────────────────────────────
  function ensureModelDefaults() {
    const p = store.prefs;
    if (!state.photoModel) state.photoModel = findModel(store.models.photo, p.photoModel)?.id || store.models.photo[0]?.id || '';
    if (!state.videoModel) state.videoModel = findModel(store.models.video, p.videoModel)?.id || store.models.video[0]?.id || '';
    if (!state.size && p.photoSize) state.size = p.photoSize;
    syncVideoParams();
  }

  function syncVideoParams() {
    const m = findModel(store.models.video, state.videoModel);
    if (!m) return;
    const r = resolveVideoParams(m, state);
    state.resolution = r.resolution; state.aspectRatio = r.aspectRatio; state.duration = r.duration;
    const c = modelCapabilities(m, 'video');
    // generate_audio only travels to models that publish the flag; anything
    // else is left to the provider rather than sent on a guess.
    if (c.audio !== true) state.generateAudio = null;
    else if (state.generateAudio === null) state.generateAudio = true;
    if (state.editAspectRatio && !c.aspectRatios.includes(state.editAspectRatio)) state.editAspectRatio = null;
  }

  function setModel(id) {
    if (state.mode === 'photo') { state.photoModel = id; ctx.savePrefs({ photoModel: id }); }
    else { state.videoModel = id; ctx.savePrefs({ videoModel: id }); syncVideoParams(); }
    if (state.mode === 'video' && state.videoMode === 'extend' && state.useRealContinuation && !caps()?.continuation) state.useRealContinuation = false;
    renderAll();
  }

  function renderModel() {
    const btn = q('#st-c-model');
    const notice = q('#st-c-model-notice');
    const meta = q('#st-c-model-meta');
    const m = currentModel();
    const nameEl = btn.querySelector('.st-model-name');
    const badgesEl = btn.querySelector('.st-model-badges');
    if (!store.modelsLoaded) {
      nameEl.textContent = store.modelsError ? 'Models unavailable' : 'Loading models…';
      badgesEl.innerHTML = '';
      btn.disabled = !store.modelsError && !store.modelsLoaded;
    } else if (!m) {
      nameEl.textContent = store.models[state.mode].length ? 'Choose a model' : 'No models available';
      badgesEl.innerHTML = '';
      btn.disabled = store.models[state.mode].length === 0;
    } else {
      nameEl.textContent = m.name;
      badgesEl.innerHTML = capabilityBadges(m, state.mode).map(b => `<span class="st-badge">${esc(b)}</span>`).join('');
      btn.disabled = false;
    }
    if (store.modelsError) {
      notice.hidden = false;
      notice.innerHTML = `${esc(store.modelsError)} <button type="button" class="st-link" id="st-c-model-retry">Retry</button>`;
      notice.querySelector('#st-c-model-retry')?.addEventListener('click', () => ctx.loadModels(true).catch(() => {}));
    } else {
      notice.hidden = true; notice.innerHTML = '';
    }
    meta.textContent = '';
    if (m && state.mode === 'photo') meta.textContent = photoPriceLabel(m.pricing) || '';
  }

  q('#st-c-model').addEventListener('click', (e) => openModelPicker(e.currentTarget));

  function openModelPicker(anchor) {
    const list = store.models[state.mode] || [];
    if (!list.length) return;
    closeMenus();
    const pop = h(`<div class="st-popover st-model-pop st-scope" role="listbox">
      <div class="st-popover-search"><input type="search" class="st-input" placeholder="Search models…" aria-label="Search models"></div>
      <div class="st-popover-list"></div>
    </div>`);
    const listEl = pop.querySelector('.st-popover-list');
    const input = pop.querySelector('input');
    const wantEdit = state.mode === 'video' && state.videoMode === 'edit';
    const wantCont = state.mode === 'video' && state.videoMode === 'extend' && state.useRealContinuation;
    const sorted = list.slice().sort((a, b) => {
      const score = (m) => (wantEdit && m.supports_video_editing ? 2 : 0) + (wantCont && m.supports_continuation ? 2 : 0);
      return score(b) - score(a) || a.name.localeCompare(b.name);
    });
    const draw = (filter = '') => {
      const f = filter.trim().toLowerCase();
      const rows = sorted.filter(m => !f || m.name.toLowerCase().includes(f) || m.id.toLowerCase().includes(f));
      listEl.innerHTML = rows.map(m => {
        const on = m.id === currentModelId();
        const dim = (wantEdit && !m.supports_video_editing) ? ' st-dim' : '';
        return `<button type="button" class="st-popover-row${on ? ' active' : ''}${dim}" data-id="${esc(m.id)}" role="option" aria-selected="${on}">
          <span class="st-popover-row-main"><span class="st-popover-row-name">${esc(m.name)}</span><span class="st-popover-row-id">${esc(m.id)}</span></span>
          <span class="st-popover-row-badges">${capabilityBadges(m, state.mode).map(b => `<span class="st-badge">${esc(b)}</span>`).join('')}</span>
          <span class="st-popover-row-check">${on ? ICONS.check : ''}</span>
        </button>`;
      }).join('') || '<div class="st-popover-empty">No models match</div>';
    };
    draw();
    input.addEventListener('input', () => draw(input.value));
    listEl.addEventListener('click', (e) => {
      const row = e.target.closest('.st-popover-row'); if (!row) return;
      setModel(row.dataset.id); closeMenus();
    });
    mountPopover(pop, anchor, { width: 360 });
    input.focus();
  }

  // ── Popover plumbing (shared by model picker + add menu) ─────────────
  let _menu = null;
  function closeMenus() {
    if (_menu) { const m = _menu; _menu = null; m.unregister(); m.el.remove(); document.removeEventListener('pointerdown', m.onDown, true); window.removeEventListener('resize', m.onResize); }
  }
  function mountPopover(el, anchor, { width = 300 } = {}) {
    el.style.zIndex = String(topPortalZ());
    document.body.appendChild(el);
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const w = Math.min(width, window.innerWidth - 16);
      el.style.width = w + 'px';
      let left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
      const below = window.innerHeight - r.bottom;
      const maxH = Math.max(160, Math.min(420, (below > 260 ? below : r.top) - 16));
      el.style.maxHeight = maxH + 'px';
      el.style.left = left + 'px';
      if (below > 260) { el.style.top = (r.bottom + 6) + 'px'; el.style.bottom = ''; }
      else { el.style.bottom = (window.innerHeight - r.top + 6) + 'px'; el.style.top = ''; }
    };
    place();
    const onDown = (e) => { if (!el.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) closeMenus(); };
    const onResize = place;
    document.addEventListener('pointerdown', onDown, true);
    window.addEventListener('resize', onResize);
    const unregister = ctx.registerMenuDismiss(() => closeMenus());
    _menu = { el, onDown, onResize, unregister };
  }

  // ── Parameters ───────────────────────────────────────────────────────
  function chip(value, label, on, extra = '') {
    return `<button type="button" class="st-chip${on ? ' active' : ''}" data-value="${esc(String(value))}" aria-pressed="${on}">${extra}${esc(label)}</button>`;
  }

  function ratioChip(ar, on) {
    const { w, h: hh } = ratioBox(ar, 16);
    return chip(ar, ar, on, `<span class="st-ratio-glyph" style="width:${w}px;height:${hh}px"></span>`);
  }

  function renderParams() {
    const box = q('#st-c-params');
    const card = q('#st-c-params-card');
    const m = currentModel();
    const c = caps();
    let html = '';
    if (state.mode === 'photo') {
      const presets = PHOTO_SIZE_PRESETS;
      const custom = state.size && !presets.includes(state.size);
      html += `<div class="st-field"><span class="st-field-label">Size <span class="st-hint">passed to the model as WIDTHxHEIGHT</span></span>
        <div class="st-chips" data-param="size">${chip('', 'Auto', !state.size)}${presets.map(s => chip(s, s, state.size === s)).join('')}
          <button type="button" class="st-chip${custom ? ' active' : ''}" data-value="__custom">Custom…</button></div>
        <input type="text" class="st-input st-input-sm" id="st-c-size-custom" placeholder="e.g. 1600x900" value="${custom ? esc(state.size) : ''}" ${custom ? '' : 'hidden'}></div>`;
      html += `<div class="st-field-row">`;
      if (c?.seed) {
        html += `<label class="st-field st-field-grow"><span class="st-field-label">Seed <span class="st-hint">blank = random</span></span>
          <span class="st-input-group"><input type="number" class="st-input st-input-sm" id="st-c-seed" inputmode="numeric" min="0" step="1" placeholder="random" value="${esc(String(state.seed ?? ''))}"><button type="button" class="st-icon-btn st-icon-btn-sm" id="st-c-seed-dice" title="Random seed">${ICONS.dice}</button></span></label>`;
      }
      html += `<label class="st-field"><span class="st-field-label">Steps <span class="st-hint">optional</span></span>
        <input type="number" class="st-input st-input-sm st-input-narrow" id="st-c-steps" inputmode="numeric" min="1" max="200" step="1" placeholder="model default" value="${esc(String(state.steps ?? ''))}"></label>`;
      html += `</div>`;
      if (!c?.seed && m) html += `<div class="st-hint st-hint-block">This model does not accept a seed.</div>`;
    } else {
      if (!m) {
        html = '<div class="st-hint st-hint-block">Choose a video model to see its options.</div>';
      } else {
        const vm = state.videoMode;
        if (vm === 'extend') {
          html += `<div class="st-field"><span class="st-field-label">Continuation</span>
            <div class="st-seg st-seg-sub" data-param="continuation">
              <button type="button" class="st-seg-btn${!state.useRealContinuation ? ' active' : ''}" data-value="frame">Last frame</button>
              <button type="button" class="st-seg-btn${state.useRealContinuation ? ' active' : ''}" data-value="real" ${c.continuation ? '' : 'disabled title="This model cannot take a video as input"'}>Real continuation</button>
            </div>
            <div class="st-hint st-hint-block">${state.useRealContinuation
              ? 'The whole source video is sent (via S3) so the model can continue the motion. Seamless, more expensive.'
              : 'The last frame of the source is extracted with FFmpeg and used as the first frame of the new segment. Works with any first-frame model.'}</div></div>`;
          html += `<label class="st-switch-row"><span>Join with source video <span class="st-hint">concatenate with FFmpeg</span></span><span class="st-switch"><input type="checkbox" id="st-c-concat" ${state.concatenate ? 'checked' : ''}><span class="st-switch-knob"></span></span></label>`;
        }
        if (vm !== 'edit') {
          if (c.resolutions.length) html += `<div class="st-field"><span class="st-field-label">Resolution</span><div class="st-chips" data-param="resolution">${c.resolutions.map(r => chip(r, r, state.resolution === r)).join('')}</div></div>`;
        }
        if (c.aspectRatios.length) {
          if (vm === 'edit') {
            html += `<div class="st-field"><span class="st-field-label">Aspect ratio <span class="st-hint">Auto keeps the source video's</span></span><div class="st-chips st-chips-ratio" data-param="editAspectRatio">${chip('', 'Auto', !state.editAspectRatio)}${c.aspectRatios.map(a => ratioChip(a, state.editAspectRatio === a)).join('')}</div></div>`;
          } else {
            html += `<div class="st-field"><span class="st-field-label">Aspect ratio</span><div class="st-chips st-chips-ratio" data-param="aspectRatio">${c.aspectRatios.map(a => ratioChip(a, state.aspectRatio === a)).join('')}</div></div>`;
          }
        }
        if (vm !== 'edit') {
          if (c.durations.length) {
            const d = c.durations;
            if (d.length <= 8) {
              html += `<div class="st-field"><span class="st-field-label">Duration</span><div class="st-chips" data-param="duration">${d.map(x => chip(x, `${x}s`, Number(state.duration) === x)).join('')}</div></div>`;
            } else {
              const idx = Math.max(0, d.indexOf(Number(state.duration)));
              html += `<div class="st-field"><span class="st-field-label">Duration <b class="st-field-value" id="st-c-duration-val">${d[idx]}s</b></span>
                <input type="range" class="st-range" id="st-c-duration" min="0" max="${d.length - 1}" step="1" value="${idx}" aria-label="Duration in seconds"><div class="st-range-ends"><span>${d[0]}s</span><span>${d[d.length - 1]}s</span></div></div>`;
            }
          }
          if (c.audio === true) {
            html += `<label class="st-switch-row"><span>Generate audio</span><span class="st-switch"><input type="checkbox" id="st-c-audio" ${state.generateAudio === true ? 'checked' : ''}><span class="st-switch-knob"></span></span></label>`;
          }
          if (!c.resolutions.length && !c.aspectRatios.length && !c.durations.length) html += `<div class="st-hint st-hint-block">OpenRouter publishes no parameter lists for this model; the provider picks defaults.</div>`;
        } else {
          html += `<div class="st-hint st-hint-block">${c.editing ? 'Describe the change; the source video is sent to the model via S3 and the result is stored as a new clip. Length and resolution follow the source.' : 'This model is not marked as a video-editing model. Pick one with the “edit” badge.'}</div>`;
        }
      }
    }
    box.innerHTML = html;
    card.hidden = !html;
    wireParams();
  }

  function wireParams() {
    const box = q('#st-c-params');
    box.querySelectorAll('.st-chips').forEach(group => {
      group.addEventListener('click', (e) => {
        const b = e.target.closest('.st-chip'); if (!b) return;
        const param = group.dataset.param;
        const v = b.dataset.value;
        if (param === 'size') {
          if (v === '__custom') {
            const inp = q('#st-c-size-custom'); inp.hidden = false; inp.focus();
            group.querySelectorAll('.st-chip').forEach(x => x.classList.toggle('active', x === b));
            return;
          }
          state.size = v; ctx.savePrefs({ photoSize: v });
        } else if (param === 'duration') state.duration = Number(v);
        else if (param === 'editAspectRatio') state.editAspectRatio = v || null;
        else state[param] = v;
        renderParams(); scheduleCost();
      });
    });
    q('#st-c-size-custom')?.addEventListener('input', (e) => { state.size = e.target.value.trim(); ctx.savePrefs({ photoSize: state.size }); });
    q('#st-c-seed')?.addEventListener('input', (e) => { state.seed = e.target.value; });
    q('#st-c-seed-dice')?.addEventListener('click', () => {
      state.seed = String(Math.floor(Math.random() * 2147483647));
      const inp = q('#st-c-seed'); if (inp) inp.value = state.seed;
    });
    q('#st-c-steps')?.addEventListener('input', (e) => { state.steps = e.target.value; });
    q('#st-c-duration')?.addEventListener('input', (e) => {
      const d = caps()?.durations || [];
      state.duration = d[Number(e.target.value)] ?? state.duration;
      const val = q('#st-c-duration-val'); if (val) val.textContent = `${state.duration}s`;
      scheduleCost();
    });
    q('#st-c-audio')?.addEventListener('change', (e) => { state.generateAudio = e.target.checked; scheduleCost(); });
    q('#st-c-concat')?.addEventListener('change', (e) => { state.concatenate = e.target.checked; });
    box.querySelector('[data-param="continuation"]')?.addEventListener('click', (e) => {
      const b = e.target.closest('.st-seg-btn'); if (!b || b.disabled) return;
      state.useRealContinuation = b.dataset.value === 'real';
      renderParams(); renderWarnings(); scheduleCost();
    });
  }

  // ── Source video (extend / edit) ─────────────────────────────────────
  function renderSource() {
    const card = q('#st-c-source-card');
    const box = q('#st-c-source');
    const show = state.mode === 'video' && state.videoMode !== 'generate';
    card.hidden = !show;
    if (!show) return;
    const s = state.source ? (ctx.getMedia(state.source.id) || state.source) : null;
    if (!s) {
      box.innerHTML = `<button type="button" class="st-source-empty" id="st-c-source-pick">${ICONS.library}<span>Choose a video from the library</span></button>`;
    } else {
      const poster = api.posterUrl(s);
      const meta = [s.width && s.height ? `${s.width}×${s.height}` : '', formatDuration(s.duration), generationModeLabel(s.generation_mode)].filter(Boolean).join(' · ');
      box.innerHTML = `<div class="st-source">
        <div class="st-source-thumb">${poster ? `<img src="${esc(poster)}" alt="">` : `<div class="st-tile-placeholder">${ICONS.film}</div>`}</div>
        <div class="st-source-info"><div class="st-source-title">${esc(s.prompt || s.filename || s.id)}</div><div class="st-source-meta">${esc(meta)}</div></div>
        <button type="button" class="st-btn st-btn-ghost st-btn-sm" id="st-c-source-pick">Change</button>
      </div>`;
    }
    q('#st-c-source-pick').addEventListener('click', () => {
      openMediaPicker(ctx, {
        title: state.videoMode === 'extend' ? 'Choose a video to extend' : 'Choose a video to edit',
        kind: 'video',
        onPick: (items) => { if (items[0]) { state.source = items[0]; renderAll(); } },
      });
    });
  }

  function setSource(media, mode) {
    if (!media || media.media_type !== 'video') return;
    if (media.job_status !== 'completed') { ctx.toast('That video is not finished yet.'); return; }
    state.source = media;
    setMode('video');
    setVideoMode(mode === 'edit' ? 'edit' : 'extend');
    promptEl.focus();
  }

  // ── References ───────────────────────────────────────────────────────
  function renderRefs() {
    const box = q('#st-c-refs');
    const meta = q('#st-c-refs-meta');
    const c = caps();
    const video = state.mode === 'video';
    const canRoles = video && state.videoMode === 'generate';
    const hint = video && state.videoMode !== 'generate' ? 'style references (sent via S3)' : (c?.maxRefs ? `up to ${c.maxRefs}` : '');
    meta.textContent = hint;
    if (!state.references.length) {
      box.innerHTML = `<button type="button" class="st-dropzone" id="st-c-refs-empty">${ICONS.image}<span>Drop images here, paste, or <b>Add</b> — from your device, the Studio library or the Gallery</span></button>`;
      q('#st-c-refs-empty').addEventListener('click', (e) => openAddMenu(e.currentTarget));
      return;
    }
    box.innerHTML = state.references.map((r, i) => `
      <div class="st-ref" data-idx="${i}">
        <img src="${esc(r.url)}" alt="" loading="lazy">
        ${r.uploading ? '<div class="st-ref-progress"><div style="width:' + Math.round((r.progress || 0) * 100) + '%"></div></div>' : ''}
        <button type="button" class="st-ref-remove" title="Remove" aria-label="Remove reference">${ICONS.close}</button>
        ${canRoles
          ? `<select class="st-ref-role" aria-label="Role">${Object.entries(ROLE_LABELS).map(([v, l]) => `<option value="${v}" ${r.role === v ? 'selected' : ''}>${l}</option>`).join('')}</select>`
          : `<span class="st-ref-role-static">${i + 1}</span>`}
      </div>`).join('');
    box.querySelectorAll('.st-ref').forEach(el => {
      const idx = Number(el.dataset.idx);
      el.querySelector('.st-ref-remove').addEventListener('click', () => { state.references.splice(idx, 1); renderRefs(); renderWarnings(); scheduleCost(); });
      el.querySelector('.st-ref-role')?.addEventListener('change', (e) => { state.references[idx].role = e.target.value; renderWarnings(); scheduleCost(); });
    });
  }

  function addReference(ref) {
    if (!ref || !ref.id) return;
    if (state.references.some(r => r.id === ref.id)) { ctx.toast('Already added'); return; }
    state.references.push({ role: 'reference', ...ref });
    renderRefs(); renderWarnings(); scheduleCost();
    q('#st-c-magic-ctx-wrap').hidden = !magicContextId();
  }

  function addReferenceFromMedia(media) {
    if (!media) return;
    if (media.media_type !== 'photo') { ctx.toast('Only photos can be used as reference images.'); return; }
    addReference({ id: media.id, url: api.mediaUrl(media), name: media.prompt || media.filename, kind: 'studio' });
    ctx.toast('Added as reference', { leadingIcon: 'check' });
  }

  async function addFiles(files) {
    for (const file of files) {
      if (!file.type.startsWith('image/')) { ctx.toast(`${file.name}: not an image`); continue; }
      const temp = { id: `pending_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`, url: URL.createObjectURL(file), name: file.name, role: 'reference', kind: 'upload', uploading: true, progress: 0 };
      state.references.push(temp);
      renderRefs();
      try {
        const rec = await api.uploadReference(file, (p) => { temp.progress = p; const bar = q(`.st-ref[data-idx="${state.references.indexOf(temp)}"] .st-ref-progress > div`); if (bar) bar.style.width = Math.round(p * 100) + '%'; });
        const objectUrl = temp.url;
        temp.id = rec.id; temp.uploading = false; temp.progress = 1;
        temp.url = api.uploadPreviewUrl(rec.id);
        setTimeout(() => { try { URL.revokeObjectURL(objectUrl); } catch { /* ignore */ } }, 2000);
      } catch (err) {
        state.references = state.references.filter(r => r !== temp);
        ctx.reportError(err, 'Upload failed');
      }
      renderRefs(); renderWarnings(); scheduleCost();
    }
    q('#st-c-magic-ctx-wrap').hidden = !magicContextId();
  }

  q('#st-c-refs-add').addEventListener('click', (e) => openAddMenu(e.currentTarget));
  q('#st-c-refs-file').addEventListener('change', (e) => { addFiles(Array.from(e.target.files || [])); e.target.value = ''; });

  function openAddMenu(anchor) {
    closeMenus();
    const pop = h(`<div class="st-popover st-menu st-scope" role="menu">
      <button type="button" class="st-menu-item" data-act="upload" role="menuitem">${ICONS.upload}<span>Upload from device</span></button>
      <button type="button" class="st-menu-item" data-act="studio" role="menuitem">${ICONS.library}<span>From Studio library</span></button>
      <button type="button" class="st-menu-item" data-act="gallery" role="menuitem">${ICONS.image}<span>From Gallery</span></button>
    </div>`);
    pop.addEventListener('click', (e) => {
      const b = e.target.closest('.st-menu-item'); if (!b) return;
      closeMenus();
      if (b.dataset.act === 'upload') q('#st-c-refs-file').click();
      else if (b.dataset.act === 'studio') openMediaPicker(ctx, { title: 'Pick reference photos', kind: 'photo', multiple: true, onPick: (items) => items.forEach(addReferenceFromMedia) });
      else openMediaPicker(ctx, {
        title: 'Pick from Gallery', kind: 'gallery', multiple: true,
        onPick: async (items) => {
          for (const g of items) {
            try {
              const file = await api.fetchAsFile(g.url, g.filename || 'gallery.png');
              await addFiles([file]);
            } catch (err) { ctx.reportError(err, 'Could not import from Gallery'); }
          }
        },
      });
    });
    mountPopover(pop, anchor, { width: 240 });
  }

  // Drag & drop anywhere on the composer
  const composerEl = q('#st-composer');
  ['dragenter', 'dragover'].forEach(ev => composerEl.addEventListener(ev, (e) => {
    if (Array.from(e.dataTransfer?.types || []).includes('Files')) { e.preventDefault(); composerEl.classList.add('st-dragover'); }
  }));
  ['dragleave', 'drop'].forEach(ev => composerEl.addEventListener(ev, (e) => {
    if (ev === 'drop') { e.preventDefault(); addFiles(Array.from(e.dataTransfer?.files || [])); }
    if (ev === 'dragleave' && composerEl.contains(e.relatedTarget)) return;
    composerEl.classList.remove('st-dragover');
  }));

  // ── Characters ───────────────────────────────────────────────────────
  function renderChars() {
    const box = q('#st-c-chars');
    const chars = store.characters || [];
    if (!store.charactersLoaded) { box.innerHTML = '<div class="st-hint st-hint-block">Loading characters…</div>'; return; }
    if (!chars.length) {
      box.innerHTML = `<div class="st-hint st-hint-block">No characters yet. <button type="button" class="st-link" id="st-c-chars-go">Create one</button> to keep faces consistent across generations.</div>`;
      q('#st-c-chars-go')?.addEventListener('click', () => ctx.showTab('characters'));
      return;
    }
    box.innerHTML = chars.map(c => {
      const on = state.characterIds.includes(c.id);
      const img = c.images?.[0] ? `<img src="${esc(api.characterImageUrl(c.id, c.images[0]))}" alt="">` : `<span class="st-avatar-fallback">${esc((c.name || '?').slice(0, 1).toUpperCase())}</span>`;
      return `<button type="button" class="st-char-chip${on ? ' active' : ''}" data-id="${esc(c.id)}" aria-pressed="${on}" title="${esc(c.name)} · ${c.images?.length || 0} photo(s)"><span class="st-avatar">${img}</span><span>${esc(c.name)}</span>${on ? ICONS.check : ''}</button>`;
    }).join('');
    box.querySelectorAll('.st-char-chip').forEach(b => b.addEventListener('click', () => {
      const id = b.dataset.id;
      const i = state.characterIds.indexOf(id);
      if (i === -1) {
        state.characterIds.push(id);
        const c = chars.find(x => x.id === id);
        if (c && !(c.images || []).length) ctx.toast(`${c.name} has no photos yet — add some in the Characters tab.`);
      } else state.characterIds.splice(i, 1);
      renderChars(); renderWarnings(); scheduleCost();
    }));
  }

  q('#st-c-chars-adv').addEventListener('click', () => {
    state.showAdvanced = !state.showAdvanced;
    q('#st-c-chars-advbox').hidden = !state.showAdvanced;
  });
  q('#st-c-char-template').addEventListener('input', (e) => ctx.savePrefs({ characterTemplate: e.target.value }));
  q('#st-c-char-suffix').addEventListener('input', (e) => ctx.savePrefs({ characterSuffix: e.target.value }));

  function syncPrefsFields() {
    const t = q('#st-c-char-template'), s = q('#st-c-char-suffix');
    if (document.activeElement !== t) t.value = store.prefs.characterTemplate || '';
    if (document.activeElement !== s) s.value = store.prefs.characterSuffix || '';
  }

  // ── Warnings / hints ─────────────────────────────────────────────────
  function renderWarnings() {
    const box = q('#st-c-warnings');
    const warns = referenceWarnings(current(), caps(), store.prefs.uploadMethod);
    const selected = selectedCharacters();
    if (selected.length) {
      const mentioned = characterNamesInPrompt(state.prompt, selected);
      selected.filter(c => !mentioned.includes(c.id)).forEach(c => warns.push(`Mention “${c.name}” in the prompt so the model maps the face to a pseudonym ([Person A], …).`));
      const missing = selected.filter(c => !(c.images || []).length);
      missing.forEach(c => warns.push(`${c.name} has no reference photos.`));
    }
    if (state.mode === 'video' && state.videoMode === 'extend' && !state.useRealContinuation) {
      // Frame extraction needs FFmpeg on the server; mention it only once per render.
      warns.push('Last-frame extension needs FFmpeg on the server.');
    }
    box.innerHTML = warns.map(w => `<div class="st-warning">${ICONS.warn}<span>${esc(w)}</span></div>`).join('');
  }

  // ── Cost estimate ────────────────────────────────────────────────────
  function scheduleCost() {
    clearTimeout(costTimer);
    costTimer = setTimeout(renderCost, 350);
  }

  async function renderCost() {
    const box = q('#st-c-cost');
    if (state.mode !== 'video' || !currentModelId() || state.videoMode === 'edit') {
      box.innerHTML = state.mode === 'photo' ? `<span class="st-cost-label">${esc(photoPriceLabel(currentModel()?.pricing) || '')}</span>` : '';
      return;
    }
    const req = buildCostRequest(current(), characterImageCount());
    const key = JSON.stringify(req);
    if (!costCache.has(key)) {
      box.innerHTML = '<span class="st-cost-label st-dim">Estimating…</span>';
      try { costCache.set(key, await api.estimateCost(req)); }
      catch (err) { costCache.set(key, { usd: null, basis: errorMessage(err) }); }
      if (costCache.size > 200) costCache = new Map(Array.from(costCache.entries()).slice(-100));
    }
    const r = costCache.get(key);
    if (JSON.stringify(buildCostRequest(current(), characterImageCount())) !== key) return; // stale
    const usd = formatUsd(r?.usd);
    box.innerHTML = usd
      ? `<span class="st-cost-value" title="${esc(r.basis || '')}">≈ ${usd}</span><span class="st-cost-label">per generation</span>`
      : `<span class="st-cost-label st-dim" title="${esc(r?.basis || '')}">${esc(r?.basis ? 'Price: ' + r.basis : 'No estimate')}</span>`;
  }

  // ── Footer / submit ──────────────────────────────────────────────────
  function generateLabel() {
    if (state.mode === 'photo') return 'Generate photo';
    if (state.videoMode === 'extend') return 'Extend video';
    if (state.videoMode === 'edit') return 'Edit video';
    return 'Generate video';
  }

  function renderFooter() {
    const btn = q('#st-c-generate');
    const label = btn.querySelector('.st-generate-label');
    const problem = validateComposer(current());
    btn.disabled = state.busy || !!problem || !store.modelsLoaded;
    btn.title = problem || '';
    q('#st-c-problem').textContent = !store.modelsLoaded && !store.modelsError ? 'Loading models…' : (problem || '');
    label.textContent = state.busy ? 'Working…' : generateLabel();
    btn.classList.toggle('st-busy', state.busy);
  }

  q('#st-c-generate').addEventListener('click', submit);

  async function submit() {
    if (state.busy) return;
    const problem = validateComposer(current());
    if (problem) { ctx.toast(problem); return; }
    if (state.references.some(r => r.uploading)) { ctx.toast('Wait for the reference upload to finish.'); return; }
    state.busy = true; renderFooter();
    const snap = current();
    const prefs = store.prefs;
    const isPhoto = state.mode === 'photo';
    const placeholder = {
      id: `local_${Date.now()}`, media_type: isPhoto ? 'photo' : 'video', job_status: 'pending',
      prompt: snap.prompt, model: snap.model, created_at: new Date().toISOString(), _local: true, _startedAt: Date.now(),
      _mode: isPhoto ? 'generate' : state.videoMode,
    };
    local.set(placeholder.id, placeholder);
    store.session.unshift(placeholder.id);
    state.activeId = placeholder.id;
    renderStage();
    scrollStageIntoView();
    try {
      let media;
      if (isPhoto) media = await api.generatePhoto(buildPhotoRequest(snap, prefs));
      else if (state.videoMode === 'extend') media = await api.extendVideo(buildExtendRequest(snap, prefs));
      else if (state.videoMode === 'edit') media = await api.editVideo(buildEditRequest(snap, prefs));
      else media = await api.generateVideo(buildVideoRequest(snap, prefs));
      local.delete(placeholder.id);
      store.session = store.session.filter(id => id !== placeholder.id);
      ctx.upsertLibrary(media, { fromGeneration: true });
      if (media.job_status === 'pending') ctx.trackJob(media);
      state.activeId = media.id;
      if (media.job_status === 'completed') ctx.toast(isPhoto ? 'Photo ready' : 'Video ready', { leadingIcon: 'check' });
      else if (media.job_status === 'failed') ctx.toast(media.error || 'Generation failed', { duration: 8000 });
    } catch (err) {
      placeholder.job_status = 'failed';
      placeholder.error = ctx.reportError(err, 'Generation failed');
      placeholder._retry = snap;
    } finally {
      state.busy = false; renderFooter(); renderStage();
    }
  }

  // ── Stage ────────────────────────────────────────────────────────────
  function stageItems() {
    return store.session.map(id => local.get(id) || ctx.getMedia(id)).filter(Boolean);
  }

  function scrollStageIntoView() {
    const stage = q('#st-stage');
    if (window.matchMedia('(max-width: 900px)').matches) stage.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  let stageSignature = '';
  function renderStage(force = false) {
    const main = q('#st-stage-main');
    const strip = q('#st-stage-strip');
    const items = stageItems();
    // Re-rendering replaces a playing <video>; skip when nothing visible changed.
    const sig = JSON.stringify([state.activeId, items.map(m => [m.id, m.job_status, m.favorite, m.error, m.thumbnail_url, m.prompt, m.width, m.file_size])]);
    if (!force && sig === stageSignature) return;
    stageSignature = sig;
    if (!items.length) {
      main.innerHTML = `<div class="st-empty">
        <div class="st-empty-icon">${ICONS.spark}</div>
        <div class="st-empty-title">Your results appear here</div>
        <div class="st-empty-text">Write a prompt, pick a model and press <b>${esc(generateLabel())}</b>. Videos render in the background — you can keep working or minimise the Studio and come back.</div>
      </div>`;
      strip.hidden = true;
      stopElapsed();
      return;
    }
    let active = items.find(m => m.id === state.activeId) || items[0];
    state.activeId = active.id;
    main.innerHTML = stageMainHtml(active);
    wireStageMain(active);
    strip.hidden = items.length < 2;
    strip.innerHTML = items.map(m => {
      const poster = m.media_type === 'video' ? api.posterUrl(m) : (m._local ? null : api.mediaUrl(m));
      const pending = m.job_status === 'pending';
      return `<button type="button" class="st-strip-item${m.id === active.id ? ' active' : ''}${pending ? ' pending' : ''}${m.job_status === 'failed' ? ' failed' : ''}" data-id="${esc(m.id)}" title="${esc(m.prompt || '')}">
        ${poster ? `<img src="${esc(poster)}" alt="">` : `<span class="st-strip-icon">${m.media_type === 'video' ? ICONS.film : ICONS.image}</span>`}
        ${pending ? '<span class="st-strip-spinner"></span>' : ''}
      </button>`;
    }).join('');
    strip.querySelectorAll('.st-strip-item').forEach(b => b.addEventListener('click', () => { state.activeId = b.dataset.id; renderStage(); }));
    if (items.some(m => m.job_status === 'pending')) startElapsed(); else stopElapsed();
  }

  function stageMainHtml(m) {
    const pending = m.job_status === 'pending';
    const failed = m.job_status === 'failed';
    const isVideo = m.media_type === 'video';
    let media = '';
    if (pending) {
      media = `<div class="st-stage-pending"><div class="st-whirl" id="st-stage-whirl"></div>
        <div class="st-stage-pending-title">${isVideo ? 'Rendering video' : 'Generating photo'}</div>
        <div class="st-stage-pending-sub">${esc(m.model || '')} · <span class="st-elapsed" data-start="${m._startedAt || parseServerDate(m.created_at) || Date.now()}">0s</span></div>
        ${isVideo && !m._local ? '<div class="st-hint">The job is finished on the server even if you close this window.</div>' : ''}</div>`;
    } else if (failed) {
      media = `<div class="st-stage-failed"><div class="st-stage-failed-title">Generation failed</div><div class="st-stage-failed-text">${esc(m.error || 'The provider reported a failure.')}</div>
        ${m._retry ? '<button type="button" class="st-btn st-btn-ghost" id="st-stage-retry">Try again</button>' : ''}</div>`;
    } else if (isVideo) {
      media = `<video class="st-stage-media" src="${esc(api.mediaUrl(m))}" ${api.posterUrl(m) ? `poster="${esc(api.posterUrl(m))}"` : ''} controls playsinline preload="metadata"></video>`;
    } else {
      media = `<img class="st-stage-media" src="${esc(api.mediaUrl(m))}" alt="${esc(m.prompt || '')}">`;
    }
    const meta = [
      m.model, m.width && m.height ? `${m.width}×${m.height}` : '', formatDuration(m.duration),
      m.fps ? `${Math.round(m.fps)} fps` : '', m.seed !== null && m.seed !== undefined ? `seed ${m.seed}` : '',
      formatBytes(m.file_size), generationModeLabel(m.generation_mode), relTime(m.created_at),
    ].filter(Boolean);
    const done = !pending && !failed && !m._local;
    const actions = done ? `<div class="st-stage-actions">
        <button type="button" class="st-icon-btn${m.favorite ? ' active' : ''}" data-act="fav" title="Favourite" aria-pressed="${!!m.favorite}">${ICONS.heart}</button>
        <button type="button" class="st-icon-btn" data-act="download" title="Download">${ICONS.download}</button>
        ${!isVideo ? `<button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="ref">${ICONS.plus}<span>Use as reference</span></button>` : ''}
        ${isVideo ? `<button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="extend">${ICONS.extend}<span>Extend</span></button><button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="edit">${ICONS.edit}<span>Edit</span></button>` : ''}
        <button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="reuse" title="Restore prompt, model and seed">${ICONS.reuse}<span>Reuse</span></button>
        <button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="open">${ICONS.library}<span>Library</span></button>
        <span class="st-grow"></span>
        <button type="button" class="st-icon-btn st-danger" data-act="delete" title="Delete">${ICONS.trash}</button>
      </div>` : '';
    return `<div class="st-stage-frame${pending ? ' is-pending' : ''}">${media}</div>
      <div class="st-stage-info">
        <div class="st-stage-prompt" title="${esc(m.prompt || '')}">${esc(m.prompt || '')}</div>
        <div class="st-stage-meta">${meta.map(x => `<span>${esc(x)}</span>`).join('')}</div>
        ${m.error && !failed ? `<div class="st-warning">${ICONS.warn}<span>${esc(m.error)}</span></div>` : ''}
        ${actions}
      </div>`;
  }

  function wireStageMain(m) {
    const main = q('#st-stage-main');
    const whirl = main.querySelector('#st-stage-whirl');
    if (whirl) { const wp = ctx.spinner.createWhirlpool(44); wp.element.style.margin = '0'; whirl.appendChild(wp.element); }
    main.querySelector('#st-stage-retry')?.addEventListener('click', () => {
      const snap = m._retry; if (!snap) return;
      local.delete(m.id); store.session = store.session.filter(id => id !== m.id);
      applySnapshot(snap); submit();
    });
    main.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', async () => {
      const act = b.dataset.act;
      const fresh = ctx.getMedia(m.id) || m;
      if (act === 'fav') {
        try { const upd = await api.patchMedia(fresh.id, { favorite: !fresh.favorite }); ctx.upsertLibrary(upd); }
        catch (err) { ctx.reportError(err); }
      } else if (act === 'download') ctx.downloadMedia(fresh);
      else if (act === 'ref') addReferenceFromMedia(fresh);
      else if (act === 'extend') setSource(fresh, 'extend');
      else if (act === 'edit') setSource(fresh, 'edit');
      else if (act === 'reuse') reuse(fresh);
      else if (act === 'open') ctx.openDetail(fresh.id);
      else if (act === 'delete') {
        const ok = await ctx.confirm('Delete this item from the Studio library?', { confirmText: 'Delete', danger: true });
        if (!ok) return;
        try { await api.deleteMedia(fresh.id); ctx.removeFromLibrary(fresh.id); ctx.toast('Deleted'); }
        catch (err) { ctx.reportError(err); }
      }
    }));
  }

  function startElapsed() {
    if (elapsedTimer) return;
    elapsedTimer = setInterval(() => {
      root.querySelectorAll('.st-elapsed').forEach(el => {
        const s = Math.max(0, Math.round((Date.now() - Number(el.dataset.start)) / 1000));
        el.textContent = s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
      });
    }, 1000);
  }
  function stopElapsed() { clearInterval(elapsedTimer); elapsedTimer = null; }

  // ── Reuse / snapshot ─────────────────────────────────────────────────
  function applySnapshot(snap) {
    state.prompt = snap.prompt || ''; promptEl.value = state.prompt; autoGrow(promptEl);
    state.negativePrompt = snap.negativePrompt || ''; negEl.value = state.negativePrompt;
    state.size = snap.size || ''; state.seed = snap.seed ?? ''; state.steps = snap.steps ?? '';
    state.resolution = snap.resolution; state.duration = snap.duration; state.generateAudio = snap.generateAudio;
    if (snap.mode === 'video' && snap.videoMode === 'edit') state.editAspectRatio = snap.aspectRatio || null;
    else state.aspectRatio = snap.aspectRatio;
    state.useRealContinuation = !!snap.useRealContinuation; state.concatenate = snap.concatenate !== false;
    state.references = (snap.references || []).slice(); state.characterIds = (snap.characterIds || []).slice();
    if (snap.mode === 'photo') state.photoModel = snap.model || state.photoModel; else state.videoModel = snap.model || state.videoModel;
    setMode(snap.mode); if (snap.mode === 'video') setVideoMode(snap.videoMode || 'generate');
  }

  function reuse(media) {
    if (!media) return;
    state.prompt = media.prompt || ''; promptEl.value = state.prompt; autoGrow(promptEl);
    if (media.media_type === 'photo') {
      if (findModel(store.models.photo, media.model)) state.photoModel = media.model;
      state.seed = media.seed !== null && media.seed !== undefined ? String(media.seed) : '';
      setMode('photo');
    } else {
      if (findModel(store.models.video, media.model)) { state.videoModel = media.model; syncVideoParams(); }
      setMode('video'); setVideoMode('generate');
    }
    ctx.toast('Prompt and model restored', { leadingIcon: 'check' });
    promptEl.focus();
  }

  // ── Render all ───────────────────────────────────────────────────────
  function renderAll() {
    const video = state.mode === 'video';
    q('#st-c-vmode').hidden = !video;
    q('#st-c-neg-toggle').hidden = video;
    if (video) negEl.hidden = true;
    q('#st-c-magic-ctx-wrap').hidden = !magicContextId();
    q('#st-c-prompt-hint').textContent = video && state.videoMode === 'edit'
      ? 'Describe the change to apply to the source video'
      : (video && state.videoMode === 'extend' ? 'Describe what happens next' : '⌘/Ctrl + Enter to generate');
    renderSource(); renderModel(); renderParams(); renderRefs(); renderChars(); syncPrefsFields(); renderWarnings(); renderFooter(); scheduleCost();
  }

  // ── Bus subscriptions ────────────────────────────────────────────────
  bus.addEventListener('models', () => { ensureModelDefaults(); renderAll(); });
  bus.addEventListener('models-error', () => { renderModel(); renderFooter(); });
  bus.addEventListener('models-loading', () => renderModel());
  bus.addEventListener('characters', () => { state.characterIds = state.characterIds.filter(id => store.characters.some(c => c.id === id)); renderChars(); renderWarnings(); });
  bus.addEventListener('prefs', () => {
    // The settings sheet changes the default models; mirror that into the
    // composer so "default" and "current" never disagree.
    let modelChanged = false;
    const pm = findModel(store.models.photo, store.prefs.photoModel);
    if (pm && pm.id !== state.photoModel) { state.photoModel = pm.id; modelChanged = true; }
    const vm = findModel(store.models.video, store.prefs.videoModel);
    if (vm && vm.id !== state.videoModel) { state.videoModel = vm.id; syncVideoParams(); modelChanged = true; }
    if (modelChanged) renderAll();
    else { syncPrefsFields(); renderWarnings(); scheduleCost(); }
  });
  bus.addEventListener('media', (e) => { const id = e.detail?.media?.id; if (id && store.session.includes(id)) renderStage(); if (state.source && state.source.id === id) renderSource(); });
  bus.addEventListener('media-removed', (e) => {
    const id = e.detail?.id;
    if (state.source?.id === id) { state.source = null; renderSource(); }
    state.references = state.references.filter(r => r.id !== id);
    renderRefs(); renderStage();
  });
  bus.addEventListener('library', () => renderStage());

  ensureModelDefaults();
  renderAll();
  renderStage();

  return {
    onShow() { renderStage(); if (!store.modelsLoaded) renderModel(); },
    setSource,
    addReferenceFromMedia,
    reuse,
    focusPrompt: () => promptEl.focus(),
  };
}
