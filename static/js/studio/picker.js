/**
 * Media Studio — in-window media picker.
 *
 * A sheet that slides over the Studio window to choose
 *   - photos from the Studio library (reference images),
 *   - videos from the Studio library (source for Extend / Edit),
 *   - images from the Odysseus Gallery (re-uploaded as references).
 *
 * Only one picker is open at a time; Escape closes it through the shared
 * escMenuStack so it dismisses before the Studio window itself.
 *
 * @module studio/picker
 */

import { formatDuration, relTime } from './payload.js';

const CLOSE_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
const CHECK_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
const FILM_ICON = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>';

let _current = null;

export function closeMediaPicker() {
  if (!_current) return;
  const p = _current; _current = null;
  p.unregister();
  p.el.classList.add('closing');
  setTimeout(() => p.el.remove(), 180);
}

/**
 * @param {object} ctx  studio context
 * @param {{title:string, kind:'photo'|'video'|'gallery', multiple?:boolean, onPick:(items:object[])=>void}} opts
 */
export function openMediaPicker(ctx, opts) {
  closeMediaPicker();
  const { api, store, esc } = ctx;
  const host = ctx.overlayHost();
  if (!host) return;
  const kind = opts.kind || 'photo';
  const multiple = !!opts.multiple;
  const selected = new Map();
  let galleryItems = [];
  let galleryOffset = 0;
  let galleryDone = false;
  let galleryLoading = false;
  let search = '';

  const el = document.createElement('div');
  el.className = 'st-sheet';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', opts.title || 'Choose media');
  el.innerHTML = `
    <div class="st-sheet-card">
      <div class="st-sheet-head">
        <div class="st-sheet-title">${esc(opts.title || 'Choose media')}</div>
        ${kind === 'gallery' ? '<input type="search" class="st-input st-sheet-search" placeholder="Search Gallery…" aria-label="Search Gallery">' : ''}
        <span class="st-grow"></span>
        ${multiple ? '<button type="button" class="st-btn st-btn-primary st-btn-sm st-sheet-confirm" disabled>Add</button>' : ''}
        <button type="button" class="st-icon-btn st-sheet-close" aria-label="Close">${CLOSE_ICON}</button>
      </div>
      <div class="st-sheet-body"><div class="st-pick-grid"></div><div class="st-sheet-foot"></div></div>
    </div>`;
  host.appendChild(el);
  const unregister = ctx.registerMenuDismiss(() => closeMediaPicker());
  _current = { el, unregister };

  const grid = el.querySelector('.st-pick-grid');
  const foot = el.querySelector('.st-sheet-foot');
  const confirmBtn = el.querySelector('.st-sheet-confirm');
  el.querySelector('.st-sheet-close').addEventListener('click', closeMediaPicker);
  el.addEventListener('click', (e) => { if (e.target === el) closeMediaPicker(); });

  const finish = (items) => {
    closeMediaPicker();
    try { opts.onPick?.(items); } catch (err) { console.error('[studio] picker onPick', err); }
  };

  if (confirmBtn) confirmBtn.addEventListener('click', () => finish(Array.from(selected.values())));

  const syncConfirm = () => {
    if (!confirmBtn) return;
    confirmBtn.disabled = selected.size === 0;
    confirmBtn.textContent = selected.size ? `Add ${selected.size}` : 'Add';
  };

  function items() {
    if (kind === 'gallery') return galleryItems;
    return store.library.items.filter(m => m.job_status === 'completed' && (kind === 'video' ? m.media_type === 'video' : m.media_type === 'photo'));
  }

  function tile(m) {
    const on = selected.has(m.id);
    let media = '';
    let caption = '';
    if (kind === 'gallery') {
      media = `<img src="${esc(m.url)}" alt="" loading="lazy">`;
      caption = m.prompt || m.caption || '';
    } else if (m.media_type === 'video') {
      const poster = api.posterUrl(m);
      media = poster ? `<img src="${esc(poster)}" alt="" loading="lazy">` : `<div class="st-tile-placeholder">${FILM_ICON}</div>`;
      caption = [formatDuration(m.duration), relTime(m.created_at)].filter(Boolean).join(' · ');
    } else {
      media = `<img src="${esc(api.mediaUrl(m))}" alt="" loading="lazy">`;
      caption = m.prompt || '';
    }
    return `<button type="button" class="st-pick-tile${on ? ' active' : ''}" data-id="${esc(m.id)}" title="${esc(m.prompt || '')}">
      ${media}<span class="st-pick-check">${CHECK_ICON}</span><span class="st-pick-caption">${esc(caption)}</span>
    </button>`;
  }

  function render() {
    const list = items();
    if (!list.length) {
      const msg = kind === 'gallery'
        ? (galleryLoading ? 'Loading Gallery…' : (search ? 'Nothing matches your search.' : 'The Gallery is empty.'))
        : (store.library.loaded ? (kind === 'video' ? 'No finished videos in the library yet. Upload one in the Library tab.' : 'No photos in the library yet.') : 'Loading library…');
      grid.innerHTML = `<div class="st-sheet-empty">${esc(msg)}</div>`;
    } else {
      grid.innerHTML = list.map(tile).join('');
    }
    grid.querySelectorAll('.st-pick-tile').forEach(t => t.addEventListener('click', () => {
      const m = list.find(x => x.id === t.dataset.id); if (!m) return;
      if (!multiple) { finish([m]); return; }
      if (selected.has(m.id)) selected.delete(m.id); else selected.set(m.id, m);
      t.classList.toggle('active', selected.has(m.id));
      syncConfirm();
    }));
    const more = kind === 'gallery' ? !galleryDone : !store.library.exhausted;
    foot.innerHTML = more ? '<button type="button" class="st-btn st-btn-ghost st-btn-sm st-sheet-more">Load more</button>' : '';
    foot.querySelector('.st-sheet-more')?.addEventListener('click', loadMore);
  }

  async function loadMore() {
    if (kind === 'gallery') {
      if (galleryLoading || galleryDone) return;
      galleryLoading = true; render();
      try {
        const data = await api.galleryLibrary(galleryOffset, 40, search);
        const page = Array.isArray(data?.items) ? data.items : [];
        galleryItems = galleryOffset === 0 ? page : [...galleryItems, ...page];
        galleryOffset += page.length;
        galleryDone = page.length < 40;
      } catch (err) {
        ctx.reportError(err, 'Could not load the Gallery');
        galleryDone = true;
      } finally {
        galleryLoading = false; render();
      }
    } else {
      await ctx.loadLibrary({ append: true, limit: 60 });
      render();
    }
  }

  const searchEl = el.querySelector('.st-sheet-search');
  if (searchEl) {
    let t = null;
    searchEl.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(() => { search = searchEl.value.trim(); galleryOffset = 0; galleryDone = false; galleryItems = []; loadMore(); }, 300);
    });
    setTimeout(() => searchEl.focus(), 50);
  }

  const onLibrary = () => { if (kind !== 'gallery') render(); };
  ctx.bus.addEventListener('library', onLibrary);
  const origUnregister = _current.unregister;
  _current.unregister = () => { origUnregister(); ctx.bus.removeEventListener('library', onLibrary); };

  if (kind === 'gallery') loadMore();
  else if (!store.library.loaded) ctx.loadLibrary().then(render);
  render();
}
