/**
 * Media Studio — pure helpers.
 *
 * Everything in here is DOM-free so the request-building and constraint
 * logic can be exercised under a bare JS engine (see
 * tests/test_studio_payload_js.py). The UI modules (composer.js, library.js,
 * characters.js) only *render* what these functions decide.
 *
 * The shapes mirror routes/studio/studio_routes.py one-to-one:
 *   PhotoGenRequest, VideoGenRequest, VideoExtendRequest, VideoEditRequest,
 *   CostEstimateRequest, MagicPromptRequest, MediaReference{id, role}.
 *
 * @module studio/payload
 */

export const MEDIA_ROLES = ['reference', 'first_frame', 'last_frame'];
export const VIDEO_MODES = ['generate', 'extend', 'edit'];

/** Server defaults, surfaced as placeholders so the user sees what applies
 *  when a field is left empty (see _apply_character_references). */
export const CHARACTER_TEMPLATE_DEFAULT =
  'Character {pseudo} is depicted in reference images {start_idx} to {end_idx}. Ensure exact facial consistency.';
export const CHARACTER_SUFFIX_DEFAULT =
  'The first reference image dictates the overall scene composition and style.';
export const MAGIC_SYSTEM_PROMPT_DEFAULT =
  'You are an expert prompt engineer for AI video and image generators. Enhance the user\'s short prompt into a highly detailed, descriptive prompt suitable for Midjourney or Sora. Output ONLY the enhanced prompt, nothing else.';

/** Photo `size` presets. The backend passes `size` straight through to
 *  OpenRouter, which has no per-model list for photos, so these are the
 *  common WIDTHxHEIGHT values plus a free-form field. */
export const PHOTO_SIZE_PRESETS = [
  '1024x1024', '1536x1024', '1024x1536', '1280x720', '720x1280',
  '1920x1080', '1080x1920', '1344x768', '768x1344',
];

export const VIDEO_UPLOAD_EXTS = ['mp4', 'mov', 'webm', 'mkv', 'm4v'];
export const CHARACTER_IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'webp'];
export const VIDEO_UPLOAD_MAX_BYTES = 100 * 1024 * 1024;
export const CHARACTER_IMAGE_MAX_BYTES = 10_000_000;

// ---------------------------------------------------------------------------
//  Models & capabilities
// ---------------------------------------------------------------------------

/** Defensive normalisation of GET /api/studio/models. */
export function normaliseModels(data) {
  const photo = Array.isArray(data?.photo) ? data.photo : [];
  const video = Array.isArray(data?.video) ? data.video : [];
  const clean = (m) => ({ ...m, id: String(m.id || ''), name: String(m.name || m.id || '') });
  return {
    photo: photo.filter(m => m && m.id).map(clean),
    video: video.filter(m => m && m.id).map(clean),
  };
}

export function findModel(list, id) {
  if (!id || !Array.isArray(list)) return null;
  return list.find(m => m.id === id) || null;
}

/**
 * Capability summary for a model entry. Video flags coming from the server
 * are TRI-STATE (true / false / null = unknown) and must not be treated as
 * false when null — a model is never filtered out on "unknown".
 */
export function modelCapabilities(model, kind) {
  if (!model) return null;
  if (kind === 'photo') {
    const maxRefs = Number.isFinite(model.max_image_references) ? model.max_image_references : null;
    return {
      kind: 'photo',
      refs: model.supports_character_reference !== false,
      maxRefs,
      seed: !!model.supports_seed,
    };
  }
  const frames = Array.isArray(model.supported_frame_images) ? model.supported_frame_images : null;
  const framesFlag = model.supports_frame_images;
  return {
    kind: 'video',
    resolutions: model.supported_resolutions || [],
    aspectRatios: model.supported_aspect_ratios || [],
    durations: (model.supported_durations || []).map(Number).filter(Number.isFinite),
    sizes: model.supported_sizes || [],
    // null = unknown, [] = known none, ['first_frame', ...] = known list
    frames,
    framesKnown: framesFlag !== null && framesFlag !== undefined,
    firstFrame: frames ? frames.includes('first_frame') : (framesFlag === null || framesFlag === undefined ? null : !!framesFlag),
    lastFrame: frames ? frames.includes('last_frame') : (framesFlag === null || framesFlag === undefined ? null : !!framesFlag),
    audio: model.supports_audio === true ? true : (model.supports_audio === false ? false : null),
    seed: model.supports_seed === true,
    refs: model.supports_character_reference === true ? true
      : (model.supports_character_reference === false ? false : null),
    maxRefs: Number.isFinite(model.max_image_references) ? model.max_image_references : null,
    continuation: !!model.supports_continuation,
    editing: !!model.supports_video_editing,
  };
}

/** Short badge list for a model row in the picker. */
export function capabilityBadges(model, kind) {
  const c = modelCapabilities(model, kind);
  if (!c) return [];
  const out = [];
  if (kind === 'photo') {
    if (c.refs) out.push(c.maxRefs ? `refs ×${c.maxRefs}` : 'refs');
    if (c.seed) out.push('seed');
    return out;
  }
  if (c.firstFrame || c.lastFrame) out.push('frames');
  if (c.refs === true) out.push(c.maxRefs ? `refs ×${c.maxRefs}` : 'refs');
  if (c.audio === true) out.push('audio');
  if (c.continuation) out.push('continue');
  if (c.editing) out.push('edit');
  if (c.seed) out.push('seed');
  return out;
}

/**
 * Keep `current` when it is one of `allowed`; otherwise fall back to the first
 * allowed value. When the model publishes no list the answer is null ("let
 * the provider pick"): the UI shows no control for it, so nothing may be sent
 * — carrying a value over from a previously selected model made OpenRouter
 * reject the request for models that do not take that parameter at all.
 */
export function pickAllowed(current, allowed) {
  if (!Array.isArray(allowed) || allowed.length === 0) return null;
  const has = allowed.some(v => String(v) === String(current));
  return has ? allowed.find(v => String(v) === String(current)) : allowed[0];
}

/** Snap a numeric duration to the nearest allowed value (mirrors the server);
 *  null when the model publishes no durations. */
export function nearestDuration(value, allowed) {
  if (!Array.isArray(allowed) || allowed.length === 0) return null;
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return allowed[0];
  const v = Number(value);
  return allowed.reduce((best, d) => (Math.abs(d - v) < Math.abs(best - v) ? d : best), allowed[0]);
}

/**
 * Resolve the video parameter triple against a model's constraint lists so
 * the UI only ever offers values the model accepts.
 */
export function resolveVideoParams(model, params = {}) {
  const c = modelCapabilities(model, 'video');
  if (!c) return { resolution: null, aspectRatio: null, duration: null };
  return {
    resolution: pickAllowed(params.resolution, c.resolutions),
    aspectRatio: pickAllowed(params.aspectRatio, c.aspectRatios),
    duration: nearestDuration(params.duration, c.durations),
  };
}

/** Width/height of a little aspect-ratio glyph (longest edge = `size`). */
export function ratioBox(ar, size = 18) {
  const m = /^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$/.exec(String(ar || '').trim());
  if (!m) return { w: size, h: size };
  const a = parseFloat(m[1]), b = parseFloat(m[2]);
  if (!(a > 0 && b > 0)) return { w: size, h: size };
  return a >= b
    ? { w: size, h: Math.max(4, Math.round(size * b / a)) }
    : { w: Math.max(4, Math.round(size * a / b)), h: size };
}

// ---------------------------------------------------------------------------
//  Request builders
// ---------------------------------------------------------------------------

function _cleanPrompt(s) { return String(s || '').trim(); }

function _mediaReferences(refs) {
  return (refs || [])
    .filter(r => r && r.id)
    .map(r => ({ id: r.id, role: MEDIA_ROLES.includes(r.role) ? r.role : 'reference' }));
}

function _characterFields(state, prefs) {
  const ids = (state.characterIds || []).filter(Boolean);
  if (!ids.length) return {};
  const out = { character_ids: ids };
  const suffix = _cleanPrompt(state.characterSuffix ?? prefs?.characterSuffix);
  const template = _cleanPrompt(state.characterTemplate ?? prefs?.characterTemplate);
  if (suffix) out.character_prompt_suffix = suffix;
  if (template) out.character_mapping_template = template;
  return out;
}

function _intOrNull(v) {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isInteger(n) ? n : null;
}

export function buildPhotoRequest(state, prefs = {}) {
  const body = { prompt: _cleanPrompt(state.prompt), model: state.model || undefined };
  const neg = _cleanPrompt(state.negativePrompt);
  if (neg) body.negative_prompt = neg;
  const size = _cleanPrompt(state.size);
  if (size) body.size = size;
  const seed = _intOrNull(state.seed);
  if (seed !== null) body.seed = seed;
  const steps = _intOrNull(state.steps);
  if (steps !== null && steps > 0) body.steps = steps;
  const refs = _mediaReferences(state.references);
  if (refs.length) body.media_references = refs;
  Object.assign(body, _characterFields(state, prefs));
  return body;
}

export function buildVideoRequest(state, prefs = {}) {
  const body = {
    prompt: _cleanPrompt(state.prompt),
    model: state.model || undefined,
    upload_method: prefs.uploadMethod === 'base64' ? 'base64' : 's3',
  };
  const neg = _cleanPrompt(state.negativePrompt);
  if (neg) body.negative_prompt = neg;
  if (state.duration !== null && state.duration !== undefined && state.duration !== '') body.duration = Number(state.duration);
  if (state.resolution) body.resolution = state.resolution;
  if (state.aspectRatio) body.aspect_ratio = state.aspectRatio;
  if (state.generateAudio === true || state.generateAudio === false) body.generate_audio = state.generateAudio;
  const refs = _mediaReferences(state.references);
  if (refs.length) body.media_references = refs;
  Object.assign(body, _characterFields(state, prefs));
  return body;
}

export function buildExtendRequest(state, prefs = {}) {
  const body = {
    source_video_id: state.sourceId,
    prompt: _cleanPrompt(state.prompt),
    model: state.model || undefined,
    use_real_continuation: !!state.useRealContinuation,
    concatenate: state.concatenate !== false,
    upload_method: prefs.uploadMethod === 'base64' ? 'base64' : 's3',
  };
  if (state.duration !== null && state.duration !== undefined && state.duration !== '') body.duration = Number(state.duration);
  if (state.resolution) body.resolution = state.resolution;
  if (state.aspectRatio) body.aspect_ratio = state.aspectRatio;
  if (state.generateAudio === true || state.generateAudio === false) body.generate_audio = state.generateAudio;
  // Extend only honours the plain "reference" role — the source video owns
  // the timeline, so frame roles are dropped rather than sent and ignored.
  const refs = _mediaReferences(state.references).filter(r => r.role === 'reference');
  if (refs.length) body.media_references = refs;
  Object.assign(body, _characterFields(state, prefs));
  return body;
}

export function buildEditRequest(state, prefs = {}) {
  const body = {
    source_video_id: state.sourceId,
    prompt: _cleanPrompt(state.prompt),
    model: state.model || undefined,
    upload_method: prefs.uploadMethod === 'base64' ? 'base64' : 's3',
  };
  if (state.aspectRatio) body.aspect_ratio = state.aspectRatio;
  const refs = _mediaReferences(state.references).filter(r => r.role === 'reference');
  if (refs.length) body.media_references = refs;
  Object.assign(body, _characterFields(state, prefs));
  return body;
}

/** CostEstimateRequest for the current video composer state. */
export function buildCostRequest(state, characterImageCount = 0) {
  const refCount = _mediaReferences(state.references).filter(r => r.role === 'reference').length
    + (characterImageCount || 0);
  return {
    model: state.model,
    duration: state.duration !== null && state.duration !== undefined && state.duration !== '' ? Number(state.duration) : null,
    resolution: state.resolution || null,
    aspect_ratio: state.aspectRatio || null,
    generate_audio: state.generateAudio === true || state.generateAudio === false ? state.generateAudio : null,
    reference_image_count: refCount,
    is_continuation: state.videoMode === 'extend' && !!state.useRealContinuation,
  };
}

export function buildMagicRequest(prompt, mediaId, prefs = {}) {
  const body = { prompt: _cleanPrompt(prompt) };
  if (mediaId) body.media_id = mediaId;
  const model = _cleanPrompt(prefs.magicModel);
  if (model) body.model = model;
  const sys = _cleanPrompt(prefs.magicSystemPrompt);
  if (sys) body.system_prompt = sys;
  return body;
}

// ---------------------------------------------------------------------------
//  Validation / hints
// ---------------------------------------------------------------------------

/**
 * Which selected characters are actually mentioned in the prompt. The server
 * replaces the name with a pseudonym using a word-boundary, case-insensitive
 * match — so a character that is selected but never named contributes its
 * reference images without a mapping in the text.
 */
export function characterNamesInPrompt(prompt, characters) {
  const text = String(prompt || '');
  const out = [];
  for (const c of characters || []) {
    if (!c || !c.name) continue;
    const re = new RegExp(`\\b${c.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
    if (re.test(text)) out.push(c.id);
  }
  return out;
}

/**
 * Non-blocking hints for the composer. Blocking problems (empty prompt,
 * missing model/source) are handled by `validateComposer`.
 */
export function referenceWarnings(state, caps, uploadMethod = 's3') {
  const warns = [];
  const refs = _mediaReferences(state.references);
  const frames = refs.filter(r => r.role !== 'reference');
  const plain = refs.filter(r => r.role === 'reference');
  if (state.mode === 'video') {
    if (state.videoMode === 'generate') {
      if (frames.length && plain.length) {
        warns.push('OpenRouter treats a request with frame images as image-to-video; plain reference images may be ignored.');
      }
      if (caps) {
        if (frames.some(r => r.role === 'first_frame') && caps.firstFrame === false) warns.push('This model does not accept a first frame.');
        if (frames.some(r => r.role === 'last_frame') && caps.lastFrame === false) warns.push('This model does not accept a last frame.');
        if (plain.length && caps.refs === false) warns.push('This model does not list reference-image support; the provider may reject them.');
        if (caps.maxRefs && plain.length > caps.maxRefs) warns.push(`This model accepts at most ${caps.maxRefs} reference image(s).`);
      }
      if (frames.filter(r => r.role === 'first_frame').length > 1) warns.push('Only one first frame is used.');
      if (frames.filter(r => r.role === 'last_frame').length > 1) warns.push('Only one last frame is used.');
    } else if (state.videoMode === 'extend') {
      if (frames.length) warns.push('Frame roles are ignored when extending — the source video sets the first frame.');
      if (plain.length) warns.push('Reference images in Extend mode are always sent through S3.');
      if (state.useRealContinuation && caps && !caps.continuation) warns.push('This model does not support real continuation; use the last-frame mode.');
    } else if (state.videoMode === 'edit') {
      if (frames.length) warns.push('Frame roles are ignored when editing a video.');
      if (plain.length) warns.push('Reference images in Edit mode are always sent through S3.');
      if (caps && !caps.editing) warns.push('This model is not a video-editing model.');
    }
    if (plain.length && uploadMethod === 'base64' && state.videoMode === 'generate') {
      warns.push('Reference images are inlined as base64 (no S3). Large images make the request slow.');
    }
  } else if (caps && caps.maxRefs && refs.length > caps.maxRefs) {
    warns.push(`This model accepts at most ${caps.maxRefs} reference image(s).`);
  }
  return warns;
}

/** Returns a human error string, or null when the composer can submit. */
export function validateComposer(state) {
  if (!_cleanPrompt(state.prompt)) return 'Write a prompt first.';
  if (!state.model) return state.mode === 'photo' ? 'Pick a photo model.' : 'Pick a video model.';
  if (state.mode === 'video' && state.videoMode !== 'generate' && !state.sourceId) {
    return state.videoMode === 'extend' ? 'Choose a source video to extend.' : 'Choose a source video to edit.';
  }
  return null;
}

// ---------------------------------------------------------------------------
//  Formatting
// ---------------------------------------------------------------------------

export function formatUsd(n) {
  if (n === null || n === undefined || !Number.isFinite(Number(n))) return null;
  const v = Number(n);
  if (v === 0) return '$0.00';
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

/** OpenRouter photo pricing is a per-model dict; `image` is USD per image. */
export function photoPriceLabel(pricing) {
  if (!pricing || typeof pricing !== 'object') return null;
  const per = Number(pricing.image);
  if (Number.isFinite(per) && per > 0) return `≈ ${formatUsd(per)} / image`;
  return null;
}

export function formatBytes(n) {
  if (!Number.isFinite(Number(n)) || n <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = Number(n), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

export function formatDuration(sec) {
  if (!Number.isFinite(Number(sec)) || sec <= 0) return '';
  const s = Math.round(Number(sec));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** The API emits naive UTC timestamps (no zone suffix); treat them as UTC. */
export function parseServerDate(iso) {
  if (!iso) return NaN;
  const s = String(iso);
  return Date.parse(s.endsWith('Z') || /[+-]\d\d:\d\d$/.test(s) ? s : s + 'Z');
}

export function relTime(iso, now = Date.now()) {
  const t = parseServerDate(iso);
  if (!Number.isFinite(t)) return '';
  const d = Math.max(0, now - t);
  const m = Math.floor(d / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const days = Math.floor(h / 24);
  if (days < 7) return `${days} d ago`;
  return new Date(t).toLocaleDateString();
}

export function jobStatusLabel(media) {
  if (!media) return '';
  if (media.job_status === 'pending') return 'Rendering';
  if (media.job_status === 'failed') return 'Failed';
  return '';
}

export function generationModeLabel(mode) {
  switch (mode) {
    case 'upload': return 'Uploaded';
    case 'extend_frame': return 'Extended (last frame)';
    case 'extend_continuation': return 'Extended (continuation)';
    case 'edit': return 'Edited';
    case 'generate': return 'Generated';
    default: return mode || '';
  }
}

/** Extract a readable message from a FastAPI error body or Error. */
export function errorMessage(err) {
  if (!err) return 'Unknown error';
  if (typeof err === 'string') return err;
  if (err.detail) {
    if (typeof err.detail === 'string') return err.detail;
    if (Array.isArray(err.detail)) return err.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
  }
  if (err.message) return err.message;
  try { return JSON.stringify(err); } catch { return String(err); }
}

export function isS3Error(msg) {
  return /\bS3\b|presigned|bucket/i.test(String(msg || ''));
}

export function isApiKeyError(msg) {
  return /api key not configured/i.test(String(msg || ''));
}

/** Byte-size / extension check for a video upload, mirroring the server. */
export function checkVideoUpload(name, size) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  if (!VIDEO_UPLOAD_EXTS.includes(ext)) return `Unsupported file type ".${ext}". Accepted: ${VIDEO_UPLOAD_EXTS.join(', ')}`;
  if (size > VIDEO_UPLOAD_MAX_BYTES) return `File is larger than ${formatBytes(VIDEO_UPLOAD_MAX_BYTES)}.`;
  return null;
}

export function checkCharacterImage(name, size) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  if (!CHARACTER_IMAGE_EXTS.includes(ext)) return `Unsupported image type ".${ext}". Use jpg, png or webp.`;
  if (size > CHARACTER_IMAGE_MAX_BYTES) return 'Image is larger than 10 MB.';
  return null;
}

/** Merge a media dict returned by the API into a list (by id), newest first. */
export function upsertMedia(list, media) {
  if (!media || !media.id) return list;
  const idx = list.findIndex(m => m.id === media.id);
  if (idx === -1) return [media, ...list];
  const next = list.slice();
  next[idx] = { ...next[idx], ...media };
  return next;
}
