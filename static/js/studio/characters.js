/**
 * Media Studio — Characters pane.
 *
 * Consistent characters are a name plus a set of reference photos. When a
 * character is selected in the composer and named in the prompt, the server
 * swaps the name for a pseudonym ([Person A]) and attaches the photos as
 * reference images (see _apply_character_references).
 *
 * Endpoints: GET/POST /api/studio/characters, POST/DELETE
 * /api/studio/characters/{id}/images[/{filename}], DELETE /api/studio/characters/{id}.
 *
 * @module studio/characters
 */

import { checkCharacterImage } from './payload.js';

const ICONS = {
  plus: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  close: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  trash: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  user: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  wand: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 4 1.5 3L20 8.5 17 10l-1.5 3L14 10l-3-1.5L14 7z"/><path d="M4 20 13 11"/></svg>',
};

export function createCharacters(root, ctx) {
  const { api, store, bus, esc } = ctx;
  const busy = new Set();

  root.innerHTML = `
    <div class="st-characters">
      <div class="st-toolbar">
        <div class="st-toolbar-title">Characters <span class="st-hint" id="st-ch-count"></span></div>
        <span class="st-grow"></span>
        <button type="button" class="st-btn st-btn-primary st-btn-sm" id="st-ch-new">${ICONS.plus}<span>New character</span></button>
      </div>
      <div class="st-characters-scroll">
        <div class="st-tipcard">
          <div class="st-tipcard-icon">${ICONS.wand}</div>
          <div><b>How it works.</b> Give a character a name and 2–5 clear photos of the face. In <b>Create</b>, select the character and write the name in your prompt — the server replaces it with a pseudonym like <code>[Person A]</code>, attaches the photos as reference images and tells the model which images show whom. Works for photos and for video models that accept reference images.</div>
        </div>
        <div class="st-char-grid" id="st-ch-grid"></div>
      </div>
      <input type="file" id="st-ch-file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple hidden>
    </div>`;

  const q = (sel) => root.querySelector(sel);
  let uploadTarget = null;

  q('#st-ch-new').addEventListener('click', async () => {
    const name = await ctx.prompt('Name the character exactly as you will write it in prompts.', { title: 'New character', placeholder: 'e.g. Anna', confirmText: 'Create', maxLength: 60 });
    if (!name) return;
    try {
      const c = await api.createCharacter(name.trim());
      store.characters = [...store.characters, { id: c.id, name: c.name, images: c.images || [] }];
      ctx.emit('characters', store.characters);
      ctx.toast(`${c.name} created — add photos next`, { leadingIcon: 'check' });
    } catch (err) { ctx.reportError(err, 'Could not create the character'); }
  });

  q('#st-ch-file').addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (uploadTarget) await addImages(uploadTarget, files);
  });

  async function addImages(charId, files) {
    const c = store.characters.find(x => x.id === charId);
    if (!c || !files.length) return;
    busy.add(charId); render();
    let added = 0;
    for (const f of files) {
      const problem = checkCharacterImage(f.name, f.size);
      if (problem) { ctx.toast(`${f.name}: ${problem}`, { duration: 6000 }); continue; }
      try {
        const res = await api.uploadCharacterImage(charId, f);
        if (res?.filename) { c.images = [...(c.images || []), res.filename]; added++; render(); }
      } catch (err) { ctx.reportError(err, `Could not upload ${f.name}`); }
    }
    busy.delete(charId);
    ctx.emit('characters', store.characters);
    if (added) ctx.toast(`${added} photo${added === 1 ? '' : 's'} added to ${c.name}`, { leadingIcon: 'check' });
    render();
  }

  async function removeImage(charId, filename) {
    const c = store.characters.find(x => x.id === charId);
    if (!c) return;
    try {
      await api.deleteCharacterImage(charId, filename);
      c.images = (c.images || []).filter(x => x !== filename);
      ctx.emit('characters', store.characters);
      render();
    } catch (err) { ctx.reportError(err, 'Could not remove the photo'); }
  }

  async function removeCharacter(charId) {
    const c = store.characters.find(x => x.id === charId);
    if (!c) return;
    const ok = await ctx.confirm(`Delete “${c.name}” and all of their reference photos?`, { confirmText: 'Delete', danger: true });
    if (!ok) return;
    try {
      await api.deleteCharacter(charId);
      store.characters = store.characters.filter(x => x.id !== charId);
      ctx.emit('characters', store.characters);
      ctx.toast('Character deleted');
    } catch (err) { ctx.reportError(err, 'Could not delete the character'); }
  }

  function card(c) {
    const imgs = c.images || [];
    const isBusy = busy.has(c.id);
    return `<article class="st-char-card" data-id="${esc(c.id)}">
      <header class="st-char-head">
        <span class="st-avatar st-avatar-lg">${imgs[0] ? `<img src="${esc(api.characterImageUrl(c.id, imgs[0]))}" alt="">` : ICONS.user}</span>
        <div class="st-char-name-wrap"><div class="st-char-name">${esc(c.name)}</div><div class="st-hint">${imgs.length} photo${imgs.length === 1 ? '' : 's'}${imgs.length < 2 ? ' · add a few more for stable faces' : ''}</div></div>
        <button type="button" class="st-icon-btn st-danger" data-act="delete" title="Delete character">${ICONS.trash}</button>
      </header>
      <div class="st-char-images">
        ${imgs.map(f => `<div class="st-char-img"><img src="${esc(api.characterImageUrl(c.id, f))}" alt="" loading="lazy"><button type="button" class="st-char-img-x" data-act="remove" data-file="${esc(f)}" title="Remove photo" aria-label="Remove photo">${ICONS.close}</button></div>`).join('')}
        <button type="button" class="st-char-add${isBusy ? ' st-busy' : ''}" data-act="add" ${isBusy ? 'disabled' : ''} title="Add photos (jpg, png, webp · ≤ 10 MB)">${ICONS.plus}<span>${isBusy ? 'Uploading…' : 'Add photos'}</span></button>
      </div>
      <div class="st-char-drop" data-act="drop" hidden>Drop photos</div>
    </article>`;
  }

  function render() {
    const grid = q('#st-ch-grid');
    const list = store.characters || [];
    q('#st-ch-count').textContent = store.charactersLoaded ? String(list.length) : '';
    if (!store.charactersLoaded) { grid.innerHTML = '<div class="st-hint st-hint-block">Loading…</div>'; return; }
    if (!list.length) {
      grid.innerHTML = `<div class="st-library-empty">${ICONS.user}<div>No characters yet. Create one to reuse the same face across photos and videos.</div></div>`;
      return;
    }
    grid.innerHTML = list.map(card).join('');
    grid.querySelectorAll('.st-char-card').forEach(cardEl => {
      const id = cardEl.dataset.id;
      cardEl.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', (e) => {
        const act = b.dataset.act;
        if (act === 'add') { uploadTarget = id; q('#st-ch-file').click(); }
        else if (act === 'remove') removeImage(id, b.dataset.file);
        else if (act === 'delete') removeCharacter(id);
        e.stopPropagation();
      }));
      let depth = 0;
      const drop = cardEl.querySelector('.st-char-drop');
      cardEl.addEventListener('dragenter', (e) => { if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return; e.preventDefault(); depth++; drop.hidden = false; });
      cardEl.addEventListener('dragover', (e) => { if (Array.from(e.dataTransfer?.types || []).includes('Files')) e.preventDefault(); });
      cardEl.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) drop.hidden = true; });
      cardEl.addEventListener('drop', (e) => { e.preventDefault(); depth = 0; drop.hidden = true; addImages(id, Array.from(e.dataTransfer?.files || [])); });
    });
  }

  bus.addEventListener('characters', render);
  bus.addEventListener('characters-error', (e) => { q('#st-ch-grid').innerHTML = `<div class="st-notice">${esc(e.detail || 'Could not load characters')}</div>`; });
  render();

  return {
    onShow() { render(); if (!store.charactersLoaded) ctx.loadCharacters(); },
  };
}
