/**
 * Media Studio — WebUI entry module.
 *
 * Owns the tool window (#studio-modal), the shared store, per-user
 * preferences and the video-job poller. The three panes are separate
 * modules (composer.js, library.js, characters.js) that receive a context
 * object and talk to each other only through the event bus below.
 *
 * Integration follows the other tool windows: the modal is registered with
 * modalManager (minimise to a dock chip, rail/sidebar badge), it is draggable
 * and resizable via windowDrag.js, and it is reachable from the sidebar,
 * the icon rail and the /studio deep link.
 *
 * The backend is used as-is; every request shape lives in payload.js.
 *
 * @module studio/studio
 */

import uiModule from '../ui.js';
import spinnerModule from '../spinner.js';
import * as Modals from '../modalManager.js';
import { makeWindowDraggable } from '../windowDrag.js';
import { registerMenuDismiss, dismissTopMenu } from '../escMenuStack.js';
import { api } from './api.js';
import { normaliseModels, upsertMedia, errorMessage, isS3Error, isApiKeyError } from './payload.js';

const MODAL_ID = 'studio-modal';
const LS_PREFS = 'odysseus-studio-prefs';
const LS_TAB = 'odysseus-studio-tab';
const POLL_MS = 5000;

// ---------------------------------------------------------------------------
//  Shared store + bus
// ---------------------------------------------------------------------------

export const bus = new EventTarget();
export function emit(type, detail) {
  try { bus.dispatchEvent(new CustomEvent(type, { detail })); } catch (e) { console.warn('[studio] emit', type, e); }
}

export const DEFAULT_PREFS = Object.freeze({
  photoModel: '',
  videoModel: '',
  uploadMethod: 's3',        // 's3' | 'base64' — how plain reference images travel
  magicModel: '',            // '' = server default (ODYSSEUS_STUDIO_MAGIC_PROMPT_MODEL)
  magicSystemPrompt: '',
  characterSuffix: '',
  characterTemplate: '',
  photoSize: '',
  resultToast: true,
});

export const store = {
  models: { photo: [], video: [] },
  modelsLoaded: false,
  modelsLoading: null,
  modelsError: null,
  characters: [],
  charactersLoaded: false,
  library: { items: [], total: 0, loaded: false, loading: false, exhausted: false },
  /** Pending video jobs keyed by media id. */
  jobs: new Map(),
  /** Ids of media generated in this browser session, newest first. */
  session: [],
  prefs: { ...DEFAULT_PREFS },
  prefsLoaded: false,
  /** Completed jobs the user has not looked at yet (drives the rail badge). */
  unseen: new Set(),
};

// ---------------------------------------------------------------------------
//  Preferences (server-side per user, mirrored in localStorage)
// ---------------------------------------------------------------------------

function _readLocalPrefs() {
  try {
    const raw = localStorage.getItem(LS_PREFS);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch { return {}; }
}

function _writeLocalPrefs(p) {
  try { localStorage.setItem(LS_PREFS, JSON.stringify(p)); } catch { /* private mode */ }
}

export async function loadPrefs() {
  if (store.prefsLoaded) return store.prefs;
  const local = _readLocalPrefs();
  store.prefs = { ...DEFAULT_PREFS, ...local };
  const remote = await api.loadPrefs();
  if (remote) store.prefs = { ...DEFAULT_PREFS, ...local, ...remote };
  store.prefsLoaded = true;
  _writeLocalPrefs(store.prefs);
  emit('prefs', store.prefs);
  return store.prefs;
}

let _prefsSaveTimer = null;
export function savePrefs(patch) {
  store.prefs = { ...store.prefs, ...patch };
  _writeLocalPrefs(store.prefs);
  emit('prefs', store.prefs);
  clearTimeout(_prefsSaveTimer);
  _prefsSaveTimer = setTimeout(() => {
    api.savePrefs(store.prefs).catch(err => console.warn('[studio] prefs save failed', err));
  }, 400);
}

// ---------------------------------------------------------------------------
//  Data loading
// ---------------------------------------------------------------------------

export function loadModels(force = false) {
  if (store.modelsLoaded && !force) return Promise.resolve(store.models);
  if (store.modelsLoading && !force) return store.modelsLoading;
  store.modelsError = null;
  emit('models-loading');
  store.modelsLoading = api.models()
    .then(data => {
      store.models = normaliseModels(data);
      store.modelsLoaded = true;
      emit('models', store.models);
      return store.models;
    })
    .catch(err => {
      store.modelsError = errorMessage(err);
      emit('models-error', store.modelsError);
      throw err;
    })
    .finally(() => { store.modelsLoading = null; });
  return store.modelsLoading;
}

export async function loadCharacters(force = false) {
  if (store.charactersLoaded && !force) return store.characters;
  try {
    const list = await api.characters();
    store.characters = Array.isArray(list) ? list : [];
    store.charactersLoaded = true;
    emit('characters', store.characters);
  } catch (err) {
    console.warn('[studio] characters load failed', err);
    emit('characters-error', errorMessage(err));
  }
  return store.characters;
}

/** Load (or append) a library page. */
export async function loadLibrary({ append = false, limit = 40 } = {}) {
  const lib = store.library;
  if (lib.loading) return lib;
  lib.loading = true;
  emit('library-loading', { append });
  try {
    const offset = append ? lib.items.length : 0;
    const data = await api.library(offset, limit);
    const page = Array.isArray(data?.media) ? data.media : [];
    lib.items = append ? [...lib.items, ...page.filter(m => !lib.items.some(x => x.id === m.id))] : page;
    lib.total = Number.isFinite(data?.total) ? data.total : lib.items.length;
    lib.exhausted = page.length < limit || lib.items.length >= lib.total;
    lib.loaded = true;
    for (const m of page) if (m.job_status === 'pending') trackJob(m, { silent: true });
    emit('library', lib);
  } catch (err) {
    emit('library-error', errorMessage(err));
  } finally {
    lib.loading = false;
  }
  return lib;
}

/** Merge one media dict into the library list and notify panes. */
export function upsertLibrary(media, { fromGeneration = false } = {}) {
  if (!media || !media.id) return;
  const lib = store.library;
  const existed = lib.items.some(m => m.id === media.id);
  lib.items = upsertMedia(lib.items, media);
  if (!existed) lib.total += 1;
  if (fromGeneration && !store.session.includes(media.id)) store.session.unshift(media.id);
  emit('media', { media, fromGeneration });
  emit('library', lib);
}

export function removeFromLibrary(id) {
  const lib = store.library;
  const before = lib.items.length;
  lib.items = lib.items.filter(m => m.id !== id);
  if (lib.items.length !== before) lib.total = Math.max(0, lib.total - 1);
  store.session = store.session.filter(x => x !== id);
  store.jobs.delete(id);
  emit('media-removed', { id });
  emit('library', lib);
}

export function getMedia(id) {
  return store.library.items.find(m => m.id === id) || store.jobs.get(id) || null;
}

// ---------------------------------------------------------------------------
//  Video job polling
// ---------------------------------------------------------------------------

let _pollTimer = null;
let _polling = false;

export function trackJob(media, { silent = false } = {}) {
  if (!media || !media.id || media.job_status !== 'pending') return;
  if (!store.jobs.has(media.id)) {
    store.jobs.set(media.id, media);
    if (!silent) emit('job-added', { media });
  }
  _syncBadges();
  _startPolling();
}

function _startPolling() {
  if (_pollTimer || store.jobs.size === 0) return;
  _pollTimer = setInterval(_pollOnce, POLL_MS);
  // First check quickly — sync completions come back in seconds.
  setTimeout(_pollOnce, 1500);
}

function _stopPolling() {
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = null;
}

async function _pollOnce() {
  if (_polling) return;
  if (store.jobs.size === 0) { _stopPolling(); return; }
  _polling = true;
  try {
    for (const id of Array.from(store.jobs.keys())) {
      let media;
      try { media = await api.job(id); } catch (err) {
        if (err && err.status === 404) { store.jobs.delete(id); removeFromLibrary(id); }
        continue;
      }
      if (!media) continue;
      if (media.job_status === 'pending') { store.jobs.set(id, media); continue; }
      store.jobs.delete(id);
      upsertLibrary(media);
      emit('job-done', { media });
      _announce(media);
    }
  } finally {
    _polling = false;
    _syncBadges();
    if (store.jobs.size === 0) _stopPolling();
  }
}

function _announce(media) {
  const visible = isStudioOpen() && !document.hidden;
  if (!visible) store.unseen.add(media.id);
  _syncBadges();
  if (store.prefs.resultToast === false) return;
  const failed = media.job_status === 'failed';
  const label = failed ? 'Video generation failed' : 'Video ready';
  uiModule.showToast(label, {
    duration: failed ? 8000 : 5000,
    leadingIcon: failed ? undefined : 'check',
    action: 'Show',
    onAction: async () => {
      await openStudio({ tab: 'library' });
      emit('open-detail', { id: media.id });
    },
  });
}

// A job that finished while the page was in the background is "unseen" until
// the Library is actually looked at again.
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && isStudioOpen() && _activeTab === 'library') markSeen();
  });
  // The global Escape arbiter in ui.js closes the *hovered tool window* before
  // it consults escMenuStack, so with the pointer over the Studio an Escape
  // meant for an open sheet (picker, settings, detail view) or popover would
  // tear the whole window down. Window-capture listeners run before the
  // document-capture arbiter, so dismiss our own overlay here and mark the
  // event handled.
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    const overlay = document.querySelector('#st-overlay-host > :not(.closing), .st-popover');
    if (!overlay) return;
    if (dismissTopMenu()) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
}

function _syncBadges() {
  const pending = store.jobs.size;
  const chip = document.getElementById('st-jobs-chip');
  if (chip) {
    chip.hidden = pending === 0;
    chip.textContent = pending === 1 ? '1 rendering' : `${pending} rendering`;
  }
  const dot = document.getElementById('studio-status-dot');
  if (dot) dot.style.display = (pending > 0 || store.unseen.size > 0) ? '' : 'none';
  const rail = document.getElementById('rail-studio');
  if (rail) {
    rail.classList.toggle('rail-notify', pending > 0 || store.unseen.size > 0);
    rail.classList.toggle('rail-notify-success', pending === 0 && store.unseen.size > 0);
  }
  const tabCount = document.getElementById('st-library-count');
  if (tabCount) tabCount.textContent = store.library.total ? String(store.library.total) : '';
}

bus.addEventListener('library', () => _syncBadges());

export function markSeen() {
  store.unseen.clear();
  _syncBadges();
}

// ---------------------------------------------------------------------------
//  Error surfacing
// ---------------------------------------------------------------------------

/** Turn an API failure into a toast with the most useful next step. */
export function reportError(err, fallback = 'Something went wrong') {
  const msg = errorMessage(err) || fallback;
  console.warn('[studio]', msg, err);
  if (isApiKeyError(msg)) {
    uiModule.showToast('OpenRouter API key missing — add OpenRouter in Settings → Models', { duration: 8000 });
    return msg;
  }
  if (isS3Error(msg)) {
    uiModule.showToast('S3 is not configured on the server (S3_ENDPOINT_URL / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY / S3_BUCKET_NAME). Reference images can be inlined instead — see Studio settings.', {
      duration: 12000,
      action: 'Settings',
      onAction: async () => { await openStudio(); openPrefs(); },
    });
    return msg;
  }
  uiModule.showToast(msg.length > 220 ? msg.slice(0, 217) + '…' : msg, { duration: 7000 });
  return msg;
}

// ---------------------------------------------------------------------------
//  Window
// ---------------------------------------------------------------------------

let _open = false;
let _built = false;
let _building = null;
let _panes = { composer: null, library: null, characters: null };
let _prefsPanel = null;
let _activeTab = 'create';

const _ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z"/><path d="m6.2 5.3 3.1 3.9"/><path d="m12.4 3.4 3.1 4"/><path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>';

function _template() {
  return `
    <div class="modal-content st-window" role="dialog" aria-label="Media Studio">
      <div class="modal-header st-header">
        <h4 class="st-title">${_ICON}<span>Studio</span></h4>
        <nav class="st-tabs" role="tablist" aria-label="Studio sections">
          <button type="button" class="st-tab active" role="tab" data-tab="create" aria-selected="true">Create</button>
          <button type="button" class="st-tab" role="tab" data-tab="library" aria-selected="false">Library <span class="st-tab-count" id="st-library-count"></span></button>
          <button type="button" class="st-tab" role="tab" data-tab="characters" aria-selected="false">Characters</button>
        </nav>
        <div class="st-header-right">
          <span id="st-jobs-chip" class="st-jobs-chip" hidden></span>
          <button type="button" class="st-icon-btn" id="st-prefs-btn" title="Studio settings" aria-label="Studio settings">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </button>
          <button type="button" class="st-icon-btn" id="st-fullscreen-btn" title="Toggle fullscreen" aria-label="Toggle fullscreen" aria-pressed="false">
            <svg class="st-fs-expand" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>
            <svg class="st-fs-collapse" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/><path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/></svg>
          </button>
          <button type="button" class="close-btn" id="close-studio-modal" aria-label="Close studio">✖</button>
        </div>
      </div>
      <div class="modal-body st-body">
        <section class="st-pane" data-pane="create" role="tabpanel"></section>
        <section class="st-pane" data-pane="library" role="tabpanel" hidden></section>
        <section class="st-pane" data-pane="characters" role="tabpanel" hidden></section>
      </div>
      <div class="st-overlay-host" id="st-overlay-host"></div>
    </div>`;
}

function _ctx() {
  return {
    api, store, bus, emit,
    ui: uiModule,
    spinner: spinnerModule,
    esc: uiModule.esc,
    toast: (msg, opts) => uiModule.showToast(msg, opts),
    confirm: (msg, opts) => uiModule.styledConfirm(msg, opts),
    prompt: (msg, opts) => uiModule.styledPrompt(msg, opts),
    reportError,
    showTab,
    openPrefs,
    loadModels, loadCharacters, loadLibrary,
    upsertLibrary, removeFromLibrary, getMedia,
    trackJob, savePrefs,
    registerMenuDismiss,
    overlayHost: () => document.getElementById('st-overlay-host'),
    windowEl: () => document.querySelector(`#${MODAL_ID} .st-window`),
    /** Composer entry points used by Library / stage actions. */
    useAsReference: (media) => { showTab('create'); _panes.composer?.addReferenceFromMedia(media); },
    startExtend: (media) => { showTab('create'); _panes.composer?.setSource(media, 'extend'); },
    startEdit: (media) => { showTab('create'); _panes.composer?.setSource(media, 'edit'); },
    reusePrompt: (media) => { showTab('create'); _panes.composer?.reuse(media); },
    openDetail: (id) => { showTab('library'); _panes.library?.openDetail(id); },
    downloadMedia,
  };
}

export function downloadMedia(media) {
  if (!media) return;
  const a = document.createElement('a');
  a.href = api.mediaUrl(media);
  a.download = media.filename || 'studio-media';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function _build(modal) {
  modal.innerHTML = _template();
  const content = modal.querySelector('.st-window');
  const header = modal.querySelector('.st-header');

  makeWindowDraggable(modal, {
    content,
    header,
    fsClass: 'st-fullscreen',
    skipSelector: 'button, input, select, .st-tabs',
    minWidth: 720,
    minHeight: 480,
  });

  modal.querySelector('#close-studio-modal').addEventListener('click', closeStudio);
  modal.querySelector('#st-fullscreen-btn').addEventListener('click', toggleFullscreen);
  modal.querySelector('#st-prefs-btn').addEventListener('click', () => openPrefs());
  modal.querySelectorAll('.st-tab').forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  const ctx = _ctx();
  const [composerMod, libraryMod, charactersMod] = await Promise.all([
    import('./composer.js'),
    import('./library.js'),
    import('./characters.js'),
  ]);
  _panes.composer = composerMod.createComposer(modal.querySelector('[data-pane="create"]'), ctx);
  _panes.library = libraryMod.createLibrary(modal.querySelector('[data-pane="library"]'), ctx);
  _panes.characters = charactersMod.createCharacters(modal.querySelector('[data-pane="characters"]'), ctx);

  bus.addEventListener('open-detail', (e) => { showTab('library'); _panes.library?.openDetail(e.detail?.id); });
  _built = true;
}

export function showTab(name) {
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;
  if (!['create', 'library', 'characters'].includes(name)) name = 'create';
  _activeTab = name;
  modal.querySelectorAll('.st-tab').forEach(btn => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  modal.querySelectorAll('.st-pane').forEach(p => { p.hidden = p.dataset.pane !== name; });
  try { localStorage.setItem(LS_TAB, name); } catch { /* ignore */ }
  if (name === 'library') { markSeen(); _panes.library?.onShow(); }
  if (name === 'characters') _panes.characters?.onShow();
  if (name === 'create') _panes.composer?.onShow();
  emit('tab', name);
}

export function toggleFullscreen(force) {
  const modal = document.getElementById(MODAL_ID);
  if (!modal) return;
  const on = typeof force === 'boolean' ? force : !modal.classList.contains('st-fullscreen');
  modal.classList.toggle('st-fullscreen', on);
  const btn = modal.querySelector('#st-fullscreen-btn');
  if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  const content = modal.querySelector('.st-window');
  if (content && !on) {
    // Leaving fullscreen: drop the fixed geometry so the window re-centres.
    content.style.position = '';
    content.style.left = '';
    content.style.top = '';
    content.style.transform = '';
  }
  if (!on && typeof window._restoreSidebarIfRouteCollapsed === 'function') {
    try { window._restoreSidebarIfRouteCollapsed(); } catch { /* ignore */ }
  }
}

export async function openPrefs() {
  if (!_prefsPanel) {
    const mod = await import('./prefsPanel.js');
    _prefsPanel = mod.createPrefsPanel(_ctx());
  }
  _prefsPanel.open();
}

/**
 * Open the Studio window. `opts.fullscreen` is used by the /studio route,
 * `opts.tab` pre-selects a section.
 */
export async function openStudio(opts = {}) {
  if (Modals.isRegistered(MODAL_ID) && Modals.isMinimized(MODAL_ID)) {
    Modals.restore(MODAL_ID);
    if (opts.tab) showTab(opts.tab);
    return;
  }
  let modal = document.getElementById(MODAL_ID);
  if (!modal) {
    modal = document.createElement('div');
    modal.id = MODAL_ID;
    modal.className = 'modal hidden st-scope';
    document.body.appendChild(modal);
  }
  if (!_built) {
    try {
      // A second click while the panes are still importing must not build twice.
      if (!_building) _building = _build(modal).finally(() => { _building = null; });
      await _building;
    } catch (err) {
      console.error('[studio] failed to build view', err);
      uiModule.showToast('Studio failed to load — see console');
      return;
    }
  }
  if (_open) {
    // Believed open but not on screen (something hid the element behind our
    // back): show it again instead of leaving the user with a dead button.
    if (modal.classList.contains('hidden') && !Modals.isMinimized(MODAL_ID)) {
      modal.classList.remove('hidden', 'modal-minimized');
      modal.style.display = '';
    }
    if (opts.tab) showTab(opts.tab);
    return;
  }
  _open = true;
  modal.classList.remove('hidden');
  const content = modal.querySelector('.st-window');
  if (content) {
    content.classList.remove('modal-closing');
    content.style.animation = '';
  }
  if (opts.fullscreen) toggleFullscreen(true);

  Modals.register(MODAL_ID, {
    railBtnId: 'rail-studio',
    sidebarBtnId: 'tool-studio-btn',
    closeFn: () => _doClose(),
    restoreFn: () => {},
  });
  document.getElementById('tool-studio-btn')?.classList.add('active');

  let tab = opts.tab;
  if (!tab) { try { tab = localStorage.getItem(LS_TAB) || 'create'; } catch { tab = 'create'; } }
  showTab(tab);

  // Kick off data — models first (the composer blocks on them), library and
  // characters in parallel. Prefs decide the default models so they go first.
  await loadPrefs();
  loadModels().catch(() => {});
  loadCharacters();
  if (!store.library.loaded) loadLibrary();
  _syncBadges();
}

function _doClose() {
  _open = false;
  const modal = document.getElementById(MODAL_ID);
  if (modal) {
    // No exit animation: modalManager.close() hides the element synchronously
    // right after this callback, so the animation never ran — but an armed
    // `animationend` listener survived and fired on the NEXT open's enter
    // animation, hiding the freshly opened window (it looked like the Studio
    // vanished on open and only a page reload brought it back).
    modal.classList.add('hidden');
    modal.querySelector('.st-window')?.classList.remove('modal-closing');
    if (modal.classList.contains('st-fullscreen')) toggleFullscreen(false);
  }
  _panes.library?.closeDetail?.();
  _prefsPanel?.close?.();
  document.getElementById('tool-studio-btn')?.classList.remove('active');
}

export function closeStudio() {
  if (!_open && !Modals.isMinimized(MODAL_ID)) return;
  if (Modals.isRegistered(MODAL_ID)) Modals.close(MODAL_ID);
  else _doClose();
}

export function isStudioOpen() {
  if (Modals.isMinimized(MODAL_ID)) return false;
  return _open;
}

// Expose for the rail / keyboard helpers that reach tools via window.*
if (typeof window !== 'undefined') {
  window.studioModule = { openStudio, closeStudio, isStudioOpen, showTab, store };
}
