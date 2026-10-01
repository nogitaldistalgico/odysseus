// static/js/passthrough.js
//
// Passthrough chats (src/passthrough.py): a chat whose model matches one of the
// `passthrough_model_patterns` settings is relayed to its model as-is. Only `*`
// and `?` are wildcards, exactly like the server, so the web UI and the server
// agree on which chats pass through. DOM-free so it can be tested on its own.

function _globRegex(pattern) {
  const body = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*/g, '.*')
    .replace(/\?/g, '.');
  return new RegExp(`^${body}$`);
}

/** True when `model` matches one of `patterns` (array or comma-separated string). */
export function isPassthroughModel(model, patterns) {
  const name = typeof model === 'string' ? model.trim().toLowerCase() : '';
  if (!name) return false;
  const list = typeof patterns === 'string' ? patterns.split(',') : patterns;
  if (!Array.isArray(list)) return false;
  return list.some(p => typeof p === 'string' && p.trim() !== ''
    && _globRegex(p.trim().toLowerCase()).test(name));
}
