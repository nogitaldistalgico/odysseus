/**
 * Media Studio — Library pane.
 *
 * Grid of everything the Studio has produced or ingested (photos, videos,
 * uploads), with filters, infinite paging over GET /api/studio/library, video
 * upload (button + drag & drop) and a detail view for one item: playable
 * media, editable prompt (PATCH), favourite, download, use-as-reference,
 * extend / edit hand-off to the composer, and soft delete.
 *
 * @module studio/library
 */

import {
  formatBytes, formatDuration, relTime, generationModeLabel, checkVideoUpload, parseServerDate,
} from './payload.js';

const ICONS = {
  heart: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.42 4.58a5.4 5.4 0 0 0-7.65 0l-.77.78-.77-.78a5.4 5.4 0 0 0-7.65 0C1.46 6.7 1.33 10.28 4 13l8 8 8-8c2.67-2.72 2.54-6.3.42-8.42z"/></svg>',
  download: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>',
  trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  close: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  play: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
  upload: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/></svg>',
  prev: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>',
  next: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
  plus: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  extend: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h12"/><path d="m12 8 4 4-4 4"/><path d="M20 5v14"/></svg>',
  edit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
  reuse: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
  warn: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
  film: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>',
};

export function createLibrary(root, ctx) {
  const { api, store, bus, esc } = ctx;

  const state = { type: 'all', favorites: false, status: 'all', detailId: null, uploading: null };
  let detailUnregister = null;
  let observer = null;
  // The detail overlay lives in the window's overlay host, OUTSIDE this pane's
  // root — `q()` cannot see it, so it is held here. (Querying it through the
  // root left an empty backdrop on screen that never rendered and never closed.)
  let detailEl = null;

  root.innerHTML = `
    <div class="st-library">
      <div class="st-toolbar">
        <div class="st-seg st-seg-sub" id="st-l-type" role="radiogroup" aria-label="Media type">
          <button type="button" class="st-seg-btn active" data-value="all" role="radio" aria-checked="true">All</button>
          <button type="button" class="st-seg-btn" data-value="photo" role="radio" aria-checked="false">Photos</button>
          <button type="button" class="st-seg-btn" data-value="video" role="radio" aria-checked="false">Videos</button>
        </div>
        <button type="button" class="st-chip" id="st-l-fav" aria-pressed="false">${ICONS.heart}<span>Favourites</span></button>
        <div class="st-chips" id="st-l-status"></div>
        <span class="st-grow"></span>
        <span class="st-hint" id="st-l-count"></span>
        <button type="button" class="st-btn st-btn-ghost st-btn-sm" id="st-l-upload">${ICONS.upload}<span>Upload video</span></button>
        <input type="file" id="st-l-file" accept=".mp4,.mov,.webm,.mkv,.m4v,video/*" multiple hidden>
      </div>
      <div class="st-upload-bar" id="st-l-upload-bar" hidden><div class="st-upload-track"><div class="st-upload-fill"></div></div><span class="st-upload-text"></span></div>
      <div class="st-notice" id="st-l-notice" hidden></div>
      <div class="st-library-scroll" id="st-l-scroll">
        <div class="st-grid" id="st-l-grid"></div>
        <div class="st-grid-foot" id="st-l-foot"></div>
      </div>
      <div class="st-dropcover" id="st-l-dropcover" hidden>${ICONS.upload}<span>Drop videos to upload</span></div>
    </div>`;

  const q = (sel) => root.querySelector(sel);

  // ── Filters ──────────────────────────────────────────────────────────
  q('#st-l-type').addEventListener('click', (e) => {
    const b = e.target.closest('.st-seg-btn'); if (!b) return;
    state.type = b.dataset.value;
    q('#st-l-type').querySelectorAll('.st-seg-btn').forEach(x => { const on = x === b; x.classList.toggle('active', on); x.setAttribute('aria-checked', on ? 'true' : 'false'); });
    render();
  });
  q('#st-l-fav').addEventListener('click', (e) => {
    state.favorites = !state.favorites;
    e.currentTarget.classList.toggle('active', state.favorites);
    e.currentTarget.setAttribute('aria-pressed', String(state.favorites));
    render();
  });
  q('#st-l-status').addEventListener('click', (e) => {
    const b = e.target.closest('.st-chip'); if (!b) return;
    state.status = state.status === b.dataset.value ? 'all' : b.dataset.value;
    render();
  });

  function filtered() {
    return store.library.items.filter(m => {
      if (state.type !== 'all' && m.media_type !== state.type) return false;
      if (state.favorites && !m.favorite) return false;
      if (state.status === 'pending' && m.job_status !== 'pending') return false;
      if (state.status === 'failed' && m.job_status !== 'failed') return false;
      return true;
    });
  }

  // ── Grid ─────────────────────────────────────────────────────────────
  function tile(m) {
    const pending = m.job_status === 'pending';
    const failed = m.job_status === 'failed';
    const isVideo = m.media_type === 'video';
    let media;
    if (pending) media = `<div class="st-tile-pending"><span class="st-tile-spinner"></span></div>`;
    else if (failed) media = `<div class="st-tile-failed">${ICONS.warn}<span>Failed</span></div>`;
    else if (isVideo) {
      // No <video> in tiles: each one fetched metadata (and, for MP4s with a
      // trailing moov atom, most of the file) over the browser's few
      // connections per host, starving the detail view. Poster or placeholder.
      const poster = api.posterUrl(m);
      media = poster ? `<img src="${esc(poster)}" alt="" loading="lazy">` : `<div class="st-tile-placeholder">${ICONS.film}</div>`;
    } else media = `<img src="${esc(api.mediaUrl(m))}" alt="" loading="lazy">`;
    const badges = [];
    if (isVideo && !pending && !failed) badges.push(`<span class="st-tile-badge">${ICONS.play}${esc(formatDuration(m.duration) || 'video')}</span>`);
    if (m.generation_mode === 'upload') badges.push('<span class="st-tile-badge st-tile-badge-soft">upload</span>');
    if (m.generation_mode === 'edit') badges.push('<span class="st-tile-badge st-tile-badge-soft">edited</span>');
    if (m.generation_mode && m.generation_mode.startsWith('extend')) badges.push('<span class="st-tile-badge st-tile-badge-soft">extended</span>');
    return `<button type="button" class="st-tile${pending ? ' is-pending' : ''}${failed ? ' is-failed' : ''}" data-id="${esc(m.id)}" title="${esc(m.prompt || m.filename || '')}">
      ${media}
      <span class="st-tile-badges">${badges.join('')}</span>
      ${m.favorite ? `<span class="st-tile-fav">${ICONS.heart}</span>` : ''}
    </button>`;
  }

  let gridSignature = '';
  function render(force = false) {
    const grid = q('#st-l-grid');
    const foot = q('#st-l-foot');
    const lib = store.library;
    const list = filtered();
    const pending = lib.items.filter(m => m.job_status === 'pending').length;
    const failed = lib.items.filter(m => m.job_status === 'failed').length;
    // Rebuilding the grid drops scroll position and refetches posters; skip no-op renders.
    const sig = JSON.stringify([state.type, state.favorites, state.status, lib.loaded, lib.loading, lib.exhausted, lib.total, pending, failed,
      list.map(m => [m.id, m.job_status, m.favorite, m.thumbnail_url, m.generation_mode, m.duration])]);
    if (!force && sig === gridSignature) return;
    gridSignature = sig;
    const statusBox = q('#st-l-status');
    statusBox.innerHTML = [
      pending ? `<button type="button" class="st-chip${state.status === 'pending' ? ' active' : ''}" data-value="pending"><span class="st-dot st-dot-live"></span>${pending} rendering</button>` : '',
      failed ? `<button type="button" class="st-chip${state.status === 'failed' ? ' active' : ''}" data-value="failed">${failed} failed</button>` : '',
    ].join('');
    if (!pending && state.status === 'pending') state.status = 'all';
    if (!failed && state.status === 'failed') state.status = 'all';
    q('#st-l-count').textContent = lib.loaded ? `${lib.total} item${lib.total === 1 ? '' : 's'}` : '';

    if (!lib.loaded && lib.loading) {
      grid.innerHTML = Array.from({ length: 12 }, () => '<div class="st-tile st-tile-skeleton"></div>').join('');
      foot.innerHTML = '';
      return;
    }
    if (!list.length) {
      const msg = lib.items.length
        ? 'Nothing matches these filters.'
        : 'The library is empty. Generate a photo or video in <b>Create</b>, or upload a video.';
      grid.innerHTML = `<div class="st-library-empty">${ICONS.film}<div>${msg}</div></div>`;
      foot.innerHTML = '';
      return;
    }
    grid.innerHTML = list.map(tile).join('');
    grid.querySelectorAll('.st-tile').forEach(t => t.addEventListener('click', () => openDetail(t.dataset.id)));
    foot.innerHTML = lib.exhausted ? '' : `<button type="button" class="st-btn st-btn-ghost st-btn-sm" id="st-l-more">${lib.loading ? 'Loading…' : 'Load more'}</button><div class="st-sentinel" id="st-l-sentinel"></div>`;
    q('#st-l-more')?.addEventListener('click', () => ctx.loadLibrary({ append: true }));
    watchSentinel();
  }

  function watchSentinel() {
    if (observer) observer.disconnect();
    const s = q('#st-l-sentinel');
    if (!s) return;
    observer = new IntersectionObserver((entries) => {
      if (entries.some(e => e.isIntersecting) && !store.library.loading && !store.library.exhausted) ctx.loadLibrary({ append: true });
    }, { root: q('#st-l-scroll'), rootMargin: '200px' });
    observer.observe(s);
  }

  // ── Upload ───────────────────────────────────────────────────────────
  q('#st-l-upload').addEventListener('click', () => q('#st-l-file').click());
  q('#st-l-file').addEventListener('change', (e) => { uploadFiles(Array.from(e.target.files || [])); e.target.value = ''; });

  async function uploadFiles(files) {
    const bar = q('#st-l-upload-bar');
    const fill = bar.querySelector('.st-upload-fill');
    const text = bar.querySelector('.st-upload-text');
    for (const file of files) {
      const problem = checkVideoUpload(file.name, file.size);
      if (problem) { ctx.toast(`${file.name}: ${problem}`, { duration: 6000 }); continue; }
      bar.hidden = false; fill.style.width = '0%'; text.textContent = `Uploading ${file.name}…`;
      try {
        const media = await api.uploadVideo(file, (p) => { fill.style.width = Math.round(p * 100) + '%'; if (p >= 1) text.textContent = `Processing ${file.name}…`; });
        ctx.upsertLibrary(media);
        ctx.toast('Video uploaded', { leadingIcon: 'check' });
      } catch (err) {
        ctx.reportError(err, 'Upload failed');
      }
    }
    bar.hidden = true;
  }

  const pane = root.firstElementChild;
  const cover = q('#st-l-dropcover');
  let dragDepth = 0;
  pane.addEventListener('dragenter', (e) => {
    if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
    e.preventDefault(); dragDepth++; cover.hidden = false;
  });
  pane.addEventListener('dragover', (e) => { if (Array.from(e.dataTransfer?.types || []).includes('Files')) e.preventDefault(); });
  pane.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) cover.hidden = true; });
  pane.addEventListener('drop', (e) => {
    e.preventDefault(); dragDepth = 0; cover.hidden = true;
    uploadFiles(Array.from(e.dataTransfer?.files || []));
  });

  // ── Detail view ──────────────────────────────────────────────────────
  function openDetail(id) {
    const m = ctx.getMedia(id);
    if (!m) { ctx.toast('Item not found'); return; }
    state.detailId = id;
    if (!detailEl) {
      const host = ctx.overlayHost();
      if (!host) return;
      detailEl = document.createElement('div');
      detailEl.id = 'st-l-detail';
      detailEl.className = 'st-detail';
      detailEl.setAttribute('role', 'dialog');
      detailEl.setAttribute('aria-label', 'Media details');
      detailEl.tabIndex = -1; // focusable so ←/→ work right after opening
      host.appendChild(detailEl);
      detailUnregister = ctx.registerMenuDismiss(() => closeDetail());
      detailEl.addEventListener('keydown', onDetailKey);
    }
    renderDetail(true);
    detailEl.focus({ preventScroll: true });
  }

  function closeDetail() {
    detailSignature = '';
    if (detailUnregister) { detailUnregister(); detailUnregister = null; }
    if (detailEl) { detailEl.querySelector('video')?.pause?.(); detailEl.remove(); detailEl = null; }
    state.detailId = null;
  }

  function onDetailKey(e) {
    if (e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  }

  function step(dir) {
    const list = filtered();
    const i = list.findIndex(m => m.id === state.detailId);
    if (i === -1) return;
    const next = list[i + dir];
    if (next) { state.detailId = next.id; renderDetail(true); }
  }

  let detailSignature = '';
  function renderDetail(force = false) {
    const el = detailEl;
    const m = ctx.getMedia(state.detailId);
    if (!el || !m) { closeDetail(); return; }
    const list = filtered();
    const i = list.findIndex(x => x.id === m.id);
    // A re-render restarts the <video>; only redraw when the item or its neighbours changed.
    const sig = JSON.stringify([m, i, list.length]);
    if (!force && sig === detailSignature) return;
    detailSignature = sig;
    const pending = m.job_status === 'pending';
    const failed = m.job_status === 'failed';
    const isVideo = m.media_type === 'video';
    let media;
    if (pending) media = `<div class="st-detail-state"><span class="st-tile-spinner st-tile-spinner-lg"></span><div>Rendering…</div><div class="st-hint">${esc(m.model || '')}</div></div>`;
    else if (failed) media = `<div class="st-detail-state st-detail-failed">${ICONS.warn}<div>Generation failed</div><div class="st-hint">${esc(m.error || '')}</div></div>`;
    else if (isVideo) media = `<video src="${esc(api.mediaUrl(m))}" ${api.posterUrl(m) ? `poster="${esc(api.posterUrl(m))}"` : ''} controls autoplay playsinline preload="auto"></video><div class="st-detail-loading"><span class="st-tile-spinner st-tile-spinner-lg"></span><span>Loading video…</span></div>`;
    else media = `<img src="${esc(api.mediaUrl(m))}" alt="${esc(m.prompt || '')}">`;

    const rows = [
      ['Type', isVideo ? 'Video' : 'Photo'],
      ['Model', m.model || '—'],
      ['Mode', generationModeLabel(m.generation_mode) || '—'],
      ['Dimensions', m.width && m.height ? `${m.width} × ${m.height}` : '—'],
      isVideo ? ['Duration', formatDuration(m.duration) || '—'] : null,
      isVideo && m.fps ? ['Frame rate', `${Math.round(m.fps * 100) / 100} fps`] : null,
      !isVideo && m.seed !== null && m.seed !== undefined ? ['Seed', String(m.seed)] : null,
      ['Size', formatBytes(m.file_size) || '—'],
      ['Created', m.created_at ? `${new Date(parseServerDate(m.created_at)).toLocaleString()} (${relTime(m.created_at)})` : '—'],
      m.source_media_id ? ['Source', m.source_media_id] : null,
      ['File', m.filename || '—'],
    ].filter(Boolean);
    const done = !pending && !failed;

    el.innerHTML = `
      <div class="st-detail-media">
        <button type="button" class="st-icon-btn st-detail-close" aria-label="Close">${ICONS.close}</button>
        <button type="button" class="st-icon-btn st-detail-nav st-detail-prev" aria-label="Previous" ${i <= 0 ? 'disabled' : ''}>${ICONS.prev}</button>
        <div class="st-detail-frame">${media}</div>
        <button type="button" class="st-icon-btn st-detail-nav st-detail-next" aria-label="Next" ${i === -1 || i >= list.length - 1 ? 'disabled' : ''}>${ICONS.next}</button>
      </div>
      <aside class="st-detail-side">
        <div class="st-detail-actions">
          <button type="button" class="st-icon-btn${m.favorite ? ' active' : ''}" data-act="fav" title="Favourite" aria-pressed="${!!m.favorite}" ${done ? '' : 'disabled'}>${ICONS.heart}</button>
          <button type="button" class="st-icon-btn" data-act="download" title="Download" ${done ? '' : 'disabled'}>${ICONS.download}</button>
          <span class="st-grow"></span>
          <button type="button" class="st-icon-btn st-danger" data-act="delete" title="Delete">${ICONS.trash}</button>
        </div>
        <label class="st-field">
          <span class="st-field-label">Prompt <span class="st-hint" id="st-d-saved"></span></span>
          <textarea class="st-input st-textarea st-detail-prompt" rows="5" ${m.generation_mode === 'upload' ? 'placeholder="Add a description…"' : ''}>${esc(m.prompt || '')}</textarea>
        </label>
        <dl class="st-meta">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
        ${m.error && !failed ? `<div class="st-warning">${ICONS.warn}<span>${esc(m.error)}</span></div>` : ''}
        <div class="st-detail-buttons">
          ${!isVideo && done ? `<button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="ref">${ICONS.plus}<span>Use as reference</span></button>` : ''}
          ${isVideo && done ? `<button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="extend">${ICONS.extend}<span>Extend</span></button><button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="edit">${ICONS.edit}<span>Edit with AI</span></button>` : ''}
          ${m.generation_mode !== 'upload' ? `<button type="button" class="st-btn st-btn-ghost st-btn-sm" data-act="reuse">${ICONS.reuse}<span>Reuse prompt</span></button>` : ''}
        </div>
      </aside>`;

    const video = el.querySelector('video');
    if (video) {
      const loading = el.querySelector('.st-detail-loading');
      const ready = () => loading?.remove();
      video.addEventListener('loadeddata', ready, { once: true });
      video.addEventListener('playing', ready, { once: true });
      video.addEventListener('error', () => {
        if (loading) loading.innerHTML = `${ICONS.warn}<span>The video could not be loaded.</span>`;
      }, { once: true });
      if (video.readyState >= 2) ready();
    }
    el.querySelector('.st-detail-close').addEventListener('click', closeDetail);
    el.querySelector('.st-detail-prev').addEventListener('click', () => step(-1));
    el.querySelector('.st-detail-next').addEventListener('click', () => step(1));
    el.querySelector('.st-detail-media').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeDetail(); });

    const promptEl = el.querySelector('.st-detail-prompt');
    let saveTimer = null;
    promptEl.addEventListener('input', () => {
      clearTimeout(saveTimer);
      const saved = el.querySelector('#st-d-saved');
      if (saved) saved.textContent = 'saving…';
      saveTimer = setTimeout(async () => {
        try {
          const upd = await api.patchMedia(m.id, { prompt: promptEl.value });
          ctx.upsertLibrary(upd);
          if (saved) { saved.textContent = 'saved'; setTimeout(() => { if (saved.textContent === 'saved') saved.textContent = ''; }, 1500); }
        } catch (err) { ctx.reportError(err, 'Could not save the prompt'); }
      }, 600);
    });

    el.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', async () => {
      const act = b.dataset.act;
      const fresh = ctx.getMedia(m.id) || m;
      if (act === 'fav') {
        try { ctx.upsertLibrary(await api.patchMedia(fresh.id, { favorite: !fresh.favorite })); } catch (err) { ctx.reportError(err); }
      } else if (act === 'download') ctx.downloadMedia(fresh);
      else if (act === 'ref') { closeDetail(); ctx.useAsReference(fresh); }
      else if (act === 'extend') { closeDetail(); ctx.startExtend(fresh); }
      else if (act === 'edit') { closeDetail(); ctx.startEdit(fresh); }
      else if (act === 'reuse') { closeDetail(); ctx.reusePrompt(fresh); }
      else if (act === 'delete') {
        const ok = await ctx.confirm('Delete this item from the Studio library? The file stays on disk but disappears from the library.', { confirmText: 'Delete', danger: true });
        if (!ok) return;
        try { await api.deleteMedia(fresh.id); ctx.removeFromLibrary(fresh.id); closeDetail(); ctx.toast('Deleted'); }
        catch (err) { ctx.reportError(err); }
      }
    }));
  }

  // ── Bus ──────────────────────────────────────────────────────────────
  bus.addEventListener('library', () => { render(); if (state.detailId) renderDetail(); });
  bus.addEventListener('library-loading', () => { if (!store.library.loaded) render(); });
  bus.addEventListener('library-error', (e) => {
    const n = q('#st-l-notice');
    n.hidden = false;
    n.innerHTML = `${esc(e.detail || 'Could not load the library')} <button type="button" class="st-link" id="st-l-retry">Retry</button>`;
    n.querySelector('#st-l-retry')?.addEventListener('click', () => { n.hidden = true; ctx.loadLibrary(); });
  });
  bus.addEventListener('media-removed', (e) => { if (state.detailId === e.detail?.id) closeDetail(); render(); });

  render();

  return {
    onShow() { render(); if (!store.library.loaded && !store.library.loading) ctx.loadLibrary(); },
    openDetail,
    closeDetail,
  };
}
