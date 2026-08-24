/**
 * OpenCode Integration — Main Client Module
 *
 * Orchestrates the /code view: SSE connection to the opencode proxy,
 * state management, API wrapper, and view lifecycle.
 *
 * @module opencode/opencode
 */

// ---------------------------------------------------------------------------
//  Sibling modules (loaded lazily on first openCodeView)
// ---------------------------------------------------------------------------
let _sessionList = null;
let _projectPicker = null;
let _chatView = null;

const BASE = '/api/opencode';
const CONFIG_BASE = '/api/opencode-config';

// ---------------------------------------------------------------------------
//  SSE connection
// ---------------------------------------------------------------------------

/** @type {EventSource|null} */
let _es = null;
/** @type {Map<string, Set<Function>>} */
const _listeners = new Map();
let _reconnectTimer = null;
let _reconnectDelay = 1000;
const _MAX_RECONNECT = 30000;

function _connectSSE() {
  if (_es) return;
  _es = new EventSource(`${BASE}/event`);

  _es.onopen = () => {
    _reconnectDelay = 1000;
    console.log('[opencode] SSE connected');
  };

  _es.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      _dispatch(evt.type, evt.properties ?? evt);
    } catch { /* ignore malformed lines (heartbeats, etc.) */ }
  };

  _es.onerror = () => {
    _es?.close();
    _es = null;
    // Reconnect with exponential backoff
    if (_isViewOpen) {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = setTimeout(() => {
        _connectSSE();
        _reconnectDelay = Math.min(_reconnectDelay * 2, _MAX_RECONNECT);
      }, _reconnectDelay);
    }
  };
}

function _disconnectSSE() {
  clearTimeout(_reconnectTimer);
  _es?.close();
  _es = null;
}

function _dispatch(type, props) {
  const cbs = _listeners.get(type);
  if (cbs) cbs.forEach(fn => { try { fn(props); } catch (err) { console.error('[opencode] listener error', err); } });
  // Also dispatch to wildcard listeners
  const wild = _listeners.get('*');
  if (wild) wild.forEach(fn => { try { fn({ type, properties: props }); } catch (err) { console.error('[opencode] wildcard listener error', err); } });
}

// ---------------------------------------------------------------------------
//  Public event API
// ---------------------------------------------------------------------------

/**
 * Register an event listener.
 * @param {string} eventType  - SSE event type (e.g. 'session.created') or '*' for all
 * @param {Function} callback
 */
export function on(eventType, callback) {
  if (!_listeners.has(eventType)) _listeners.set(eventType, new Set());
  _listeners.get(eventType).add(callback);
}

/** Unregister an event listener. */
export function off(eventType, callback) {
  _listeners.get(eventType)?.delete(callback);
}

// ---------------------------------------------------------------------------
//  API client — thin fetch wrappers, no transformation
// ---------------------------------------------------------------------------

async function _fetch(path, opts = {}) {
  const url = `${BASE}/${path.replace(/^\//, '')}`;
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`opencode ${resp.status}: ${detail}`);
  }
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) return resp.json();
  if (resp.status === 204) return null;
  return resp.text();
}

/** @type {OpenCodeClient} */
export const client = {
  // Sessions
  listSessions: (directory) =>
    _fetch(`session${directory ? `?directory=${encodeURIComponent(directory)}` : ''}`),
  createSession: (title, directory) =>
    _fetch(`session${directory ? `?directory=${encodeURIComponent(directory)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ title: title || undefined }),
    }),
  getSession: (id) => _fetch(`session/${id}`),
  updateSession: (id, body) => _fetch(`session/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteSession: (id) => _fetch(`session/${id}`, { method: 'DELETE' }),
  getMessages: (id) => _fetch(`session/${id}/message`),
  promptAsync: (id, text, agent = 'plan') =>
    _fetch(`session/${id}/prompt_async`, {
      method: 'POST',
      body: JSON.stringify({ parts: [{ type: 'text', text }], agent }),
    }),
  promptSync: (id, text, agent = 'plan', model = undefined) => {
    const dir = localStorage.getItem('oc_active_project');
    const headers = { 'Content-Type': 'application/json' };
    if (dir) headers['x-opencode-directory'] = dir;
    const bodyObj = { parts: [{ type: 'text', text }], agent };
    if (model) bodyObj.model = model;
    return fetch(`${BASE}/session/${id}/message`, {
      method: 'POST',
      headers,
      body: JSON.stringify(bodyObj),
    });
  },
  getProviders: () => {
    const dir = localStorage.getItem('oc_active_project');
    const headers = {};
    if (dir) headers['x-opencode-directory'] = dir;
    return _fetch('provider', { headers });
  },
  getModels: (providerId) => {
    const dir = localStorage.getItem('oc_active_project');
    const headers = {};
    if (dir) headers['x-opencode-directory'] = dir;
    return _fetch(`provider/${providerId}/model`, { headers });
  },
  abort: (id) => _fetch(`session/${id}/abort`, { method: 'POST' }),
  getStatus: (id) => _fetch(`session/${id}/status`),
  getDiff: (id) => _fetch(`session/${id}/diff`),
  getTodo: (id) => _fetch(`session/${id}/todo`),
  revert: (id) => _fetch(`session/${id}/revert`, { method: 'POST' }),
  summarize: (id) => _fetch(`session/${id}/summarize`, { method: 'POST' }),

  // Permissions & Questions
  approvePermission: (id, approved, remember = false) =>
    _fetch(`permission/${id}/reply`, {
      method: 'POST',
      body: JSON.stringify({ approved, remember }),
    }),
  answerQuestion: (id, answer) =>
    _fetch(`question/${id}/reply`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),

  // Files & Project
  listFiles: (directory) =>
    _fetch(`file${directory ? `?directory=${encodeURIComponent(directory)}` : ''}`),
  readFile: (path, directory) => {
    const params = new URLSearchParams({ path });
    if (directory) params.set('directory', directory);
    return _fetch(`file/content?${params}`);
  },
  getProject: (directory) =>
    _fetch(`project${directory ? `?directory=${encodeURIComponent(directory)}` : ''}`),

  // Providers & Config
  listProviders: () => _fetch('provider'),
  getConfig: () => _fetch('config'),

  // Odysseus-side config
  getLocalConfig: () => fetch(`${CONFIG_BASE}`).then(r => r.json()),
  healthCheck: () => fetch(`${CONFIG_BASE}/health`).then(r => r.json()),
  setProjects: (projects) =>
    fetch(`${CONFIG_BASE}/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projects }),
    }).then(r => r.json()),
  reloadProxy: () => fetch(`${CONFIG_BASE}/reload`, { method: 'POST' }).then(r => r.json()),
};

// ---------------------------------------------------------------------------
//  View lifecycle
// ---------------------------------------------------------------------------

let _isViewOpen = false;

/**
 * Open the /code fullscreen modal view.
 * Lazy-loads sibling UI modules on first call.
 */
export async function openCodeView() {
  const modal = document.getElementById('opencode-modal');
  if (!modal) { console.error('[opencode] modal element not found'); return; }

  // Show modal
  modal.classList.remove('hidden');
  modal.classList.add('opencode-fullscreen');
  _isViewOpen = true;

  // Connect SSE
  _connectSSE();

  // Lazy-load UI modules
  if (!_sessionList || !_chatView || !_projectPicker) {
    try {
      const [slMod, ppMod, cvMod] = await Promise.all([
        import('./sessionList.js'),
        import('./projectPicker.js'),
        import('./chatView.js'),
      ]);

      const body = document.getElementById('opencode-body');
      if (!body) return;

      // Build layout if not already built
      if (!body.querySelector('.oc-sidebar')) {
        body.innerHTML = `
          <div class="oc-sidebar">
            <div class="oc-sidebar-header" id="oc-project-picker-container"></div>
            <div class="oc-session-list-container" id="oc-session-list-container"></div>
          </div>
          <div class="oc-main">
            <div class="oc-chat-container" id="oc-chat-container"></div>
          </div>
        `;
      }

      const pickerEl = document.getElementById('oc-project-picker-container');
      const listEl = document.getElementById('oc-session-list-container');
      const chatEl = document.getElementById('oc-chat-container');

      _chatView = cvMod.createChatView(chatEl, { client });

      _sessionList = slMod.createSessionList(listEl, {
        client,
        onSessionSelect: (sessionId) => _chatView.loadSession(sessionId),
      });

      _projectPicker = ppMod.createProjectPicker(pickerEl, {
        onProjectChange: (dir) => _sessionList.setDirectory(dir),
      });

      // Wire SSE events to UI components
      on('*', (evt) => {
        _sessionList?.handleEvent?.(evt);
        _chatView?.handleEvent?.(evt);
      });
    } catch (err) {
      console.error('[opencode] Failed to load UI modules:', err);
    }
  }

  // Close button
  const closeBtn = document.getElementById('close-opencode-modal');
  if (closeBtn && !closeBtn._ocWired) {
    closeBtn._ocWired = true;
    closeBtn.addEventListener('click', closeCodeView);
  }

  // Escape key
  if (!modal._ocEsc) {
    modal._ocEsc = true;
    modal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeCodeView();
    });
  }
}

/** Close the /code view and disconnect SSE. */
export function closeCodeView() {
  const modal = document.getElementById('opencode-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('opencode-fullscreen');
  }
  _isViewOpen = false;
  _disconnectSSE();
}

/** @returns {boolean} */
export function isOpen() {
  return _isViewOpen;
}
