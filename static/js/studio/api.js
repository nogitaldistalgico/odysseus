/**
 * Media Studio — API client.
 *
 * Thin fetch wrappers over routes/studio/studio_routes.py plus the three
 * neighbouring endpoints the Studio UI leans on:
 *
 *   POST /api/upload               — reference images are ordinary uploads;
 *                                    the returned id is resolvable by the
 *                                    studio (see _resolve_media_path)
 *   GET  /api/gallery/library      — "pick from Gallery" source for references
 *   GET/PUT /api/prefs/{key}       — per-user UI preferences
 *
 * No transformation happens here; payload shapes live in payload.js.
 *
 * @module studio/api
 */

const BASE = '/api/studio';
export const PREFS_KEY = 'studio_web_prefs';

async function _parse(resp) {
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    try { return await resp.json(); } catch { return null; }
  }
  try { return await resp.text(); } catch { return null; }
}

/** Throws an Error carrying `.status` and `.detail` on non-2xx. */
async function _request(url, opts = {}) {
  const resp = await fetch(url, opts);
  const body = await _parse(resp);
  if (!resp.ok) {
    const detail = (body && typeof body === 'object' && body.detail !== undefined) ? body.detail
      : (typeof body === 'string' && body.trim()) ? body.trim() : resp.statusText;
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    err.status = resp.status;
    err.detail = detail;
    throw err;
  }
  return body;
}

function _json(method, url, payload) {
  return _request(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });
}

/**
 * Multipart upload with progress. Returns a promise resolving to the parsed
 * JSON body; `onProgress(fraction)` fires from XHR upload events.
 */
function _upload(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;
    if (xhr.upload && typeof onProgress === 'function') {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      });
    }
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { body = xhr.responseText; }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
      const detail = body && typeof body === 'object' && body.detail !== undefined ? body.detail : (body || xhr.statusText);
      const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      err.status = xhr.status;
      err.detail = detail;
      reject(err);
    };
    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.onabort = () => reject(new Error('Upload cancelled'));
    xhr.send(formData);
  });
}

export const api = {
  // ── Models ────────────────────────────────────────────────────────────
  models: () => _request(`${BASE}/models`),
  modelConstraints: (modelId) => _request(`${BASE}/model-constraints/${modelId}`),
  estimateCost: (payload) => _json('POST', `${BASE}/estimate-cost`, payload),

  // ── Library ───────────────────────────────────────────────────────────
  library: (offset = 0, limit = 40) => _request(`${BASE}/library?offset=${offset}&limit=${limit}`),
  patchMedia: (id, patch) => _json('PATCH', `${BASE}/media/${encodeURIComponent(id)}`, patch),
  deleteMedia: (id) => _request(`${BASE}/media/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  job: (id) => _request(`${BASE}/jobs/${encodeURIComponent(id)}`),
  mediaUrl: (media) => media?.url || (media?.filename ? `${BASE}/media/${media.filename}` : ''),
  posterUrl: (media) => media?.thumbnail_url || null,

  // ── Generation ────────────────────────────────────────────────────────
  generatePhoto: (payload) => _json('POST', `${BASE}/generate/photo`, payload),
  generateVideo: (payload) => _json('POST', `${BASE}/generate/video`, payload),
  extendVideo: (payload) => _json('POST', `${BASE}/extend-video`, payload),
  editVideo: (payload) => _json('POST', `${BASE}/edit-video`, payload),
  magicPrompt: (payload) => _json('POST', `${BASE}/magic-prompt`, payload),

  // ── Uploads ───────────────────────────────────────────────────────────
  /** Video into the Studio library (mp4/mov/webm/mkv/m4v ≤ 100 MB). */
  uploadVideo: (file, onProgress) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    return _upload(`${BASE}/upload`, fd, onProgress);
  },
  /**
   * Reference image via the general upload endpoint. Returns the first
   * uploaded file record ({id, name, mime, width, height, ...}); `id` is the
   * on-disk filename the studio resolves through UPLOAD_DIR.
   */
  uploadReference: async (file, onProgress) => {
    const fd = new FormData();
    fd.append('files', file, file.name);
    const body = await _upload('/api/upload', fd, onProgress);
    const rec = body && Array.isArray(body.files) ? body.files[0] : null;
    if (!rec || !rec.id) throw new Error('Upload returned no file id');
    return rec;
  },
  uploadPreviewUrl: (id, thumb = true) => `/api/upload/${encodeURIComponent(id)}${thumb ? '?thumb=1' : ''}`,

  // ── Characters ────────────────────────────────────────────────────────
  characters: () => _request(`${BASE}/characters`),
  createCharacter: (name) => _json('POST', `${BASE}/characters`, { name }),
  deleteCharacter: (id) => _request(`${BASE}/characters/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  uploadCharacterImage: (id, file, onProgress) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    return _upload(`${BASE}/characters/${encodeURIComponent(id)}/images`, fd, onProgress);
  },
  deleteCharacterImage: (id, filename) =>
    _request(`${BASE}/characters/${encodeURIComponent(id)}/images/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  characterImageUrl: (id, filename) =>
    `${BASE}/characters/${encodeURIComponent(id)}/images/${encodeURIComponent(filename)}`,

  // ── Gallery (picker source) ───────────────────────────────────────────
  galleryLibrary: (offset = 0, limit = 40, search = '') => {
    const q = new URLSearchParams({ offset: String(offset), limit: String(limit), sort: 'recent' });
    if (search) q.set('search', search);
    return _request(`/api/gallery/library?${q.toString()}`);
  },
  /** Fetch a same-origin file as a File so it can be re-uploaded. */
  fetchAsFile: async (url, name) => {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`Could not read ${name || url} (${resp.status})`);
    const blob = await resp.blob();
    return new File([blob], name || url.split('/').pop() || 'image.png', { type: blob.type || 'image/png' });
  },

  // ── Preferences ───────────────────────────────────────────────────────
  loadPrefs: async () => {
    try {
      const body = await _request(`/api/prefs/${PREFS_KEY}`);
      return body && body.value && typeof body.value === 'object' ? body.value : null;
    } catch { return null; }
  },
  savePrefs: (value) => _json('PUT', `/api/prefs/${PREFS_KEY}`, { value }),
};

export default api;
