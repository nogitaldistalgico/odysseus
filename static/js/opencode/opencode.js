/**
 * OpenCode Integration — Main Client Module
 *
 * Orchestrates the /code view: SSE connection to the opencode proxy,
 * state management, API wrapper, and view lifecycle.
 *
 * All calls target opencode's v1 REST surface, which resolves the active
 * project from a `directory` query parameter (see
 * packages/opencode/src/server/routes/instance/httpapi/middleware/workspace-routing.ts).
 * That parameter is therefore attached to *every* request here, including the
 * event stream — opencode filters its event bus by instance directory, so a
 * stream opened without it only ever reports on the server's own cwd.
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

/** Single source of truth for the persisted project. */
export const ACTIVE_PROJECT_KEY = 'oc_active_project';

// ---------------------------------------------------------------------------
//  Active project
// ---------------------------------------------------------------------------

let _directory = null;
try { _directory = localStorage.getItem(ACTIVE_PROJECT_KEY); } catch { /* private mode */ }

/** @returns {string|null} the directory every request is scoped to. */
export function getDirectory() {
  return _directory;
}

/**
 * Switch the active project. Reconnects the event stream, because opencode
 * binds each stream to one directory for its lifetime.
 * @param {string|null} dir
 */
export function setDirectory(dir) {
  if (dir === _directory) return;
  _directory = dir || null;
  try {
    if (_directory) localStorage.setItem(ACTIVE_PROJECT_KEY, _directory);
    else localStorage.removeItem(ACTIVE_PROJECT_KEY);
  } catch { /* private mode */ }
  if (_isViewOpen) {
    _disconnectSSE();
    _connectSSE();
  }
}

/** Append the active directory to a proxy path. */
function _url(path, extraParams) {
  const params = new URLSearchParams(extraParams || {});
  if (_directory) params.set('directory', _directory);
  const qs = params.toString();
  return `${BASE}/${path.replace(/^\//, '')}${qs ? (path.includes('?') ? '&' : '?') + qs : ''}`;
}

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
  // EventSource cannot set headers, so the directory has to travel as a query
  // parameter — the header form (x-opencode-directory) is unavailable here.
  _es = new EventSource(_url('event'));

  _es.onopen = () => {
    _reconnectDelay = 1000;
    _setConnectionBadge(true);
  };

  _es.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      // opencode emits {id, type, properties}. Dispatch the payload only.
      _dispatch(evt.type, evt.properties ?? {});
    } catch { /* ignore malformed lines (heartbeats, etc.) */ }
  };

  _es.onerror = () => {
    _es?.close();
    _es = null;
    _setConnectionBadge(false);
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

function _setConnectionBadge(ok) {
  const badge = document.getElementById('opencode-connection-badge');
  if (!badge) return;
  badge.textContent = ok ? 'connected' : 'reconnecting…';
  badge.dataset.state = ok ? 'ok' : 'down';
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
 * @param {string} eventType  - SSE event type (e.g. 'session.updated') or '*' for all
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
  const resp = await fetch(_url(path), {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`opencode ${resp.status}: ${detail}`);
  }
  if (resp.status === 204) return null;
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) return resp.json();
  return resp.text();
}

/**
 * opencode's REST client.
 *
 * Payload shapes are pinned to packages/sdk/openapi.json — every request body
 * here is validated server-side with `additionalProperties: false`, so an extra
 * or misnamed field is a 400, not a silently ignored value.
 */
export const client = {
  // Sessions
  listSessions: () => _fetch('session'),
  createSession: (title) =>
    _fetch('session', {
      method: 'POST',
      // `directory` belongs in the query string (added by _url); the body
      // schema rejects it outright.
      body: JSON.stringify(title ? { title } : {}),
    }),
  getSession: (id) => _fetch(`session/${id}`),
  updateSession: (id, body) => _fetch(`session/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteSession: (id) => _fetch(`session/${id}`, { method: 'DELETE' }),
  getMessages: (id) => _fetch(`session/${id}/message`),

  /** Fire-and-forget prompt; the reply arrives over the event stream. */
  promptAsync: (id, text, agent, model) => {
    const body = { parts: [{ type: 'text', text }] };
    if (agent) body.agent = agent;
    if (model) body.model = model;
    return _fetch(`session/${id}/prompt_async`, { method: 'POST', body: JSON.stringify(body) });
  },

  abort: (id) => _fetch(`session/${id}/abort`, { method: 'POST' }),
  /** Status of *all* sessions — opencode has no per-session status route. */
  getStatus: () => _fetch('session/status'),
  getDiff: (id) => _fetch(`session/${id}/diff`),
  getTodo: (id) => _fetch(`session/${id}/todo`),
  /** Revert the session to just before `messageID` (required by opencode). */
  revert: (id, messageID) =>
    _fetch(`session/${id}/revert`, { method: 'POST', body: JSON.stringify({ messageID }) }),
  unrevert: (id) => _fetch(`session/${id}/unrevert`, { method: 'POST' }),
  summarize: (id) => _fetch(`session/${id}/summarize`, { method: 'POST' }),

  // Permissions & Questions
  /** @param {'once'|'always'|'reject'} reply */
  replyPermission: (requestID, reply, message) =>
    _fetch(`permission/${requestID}/reply`, {
      method: 'POST',
      body: JSON.stringify(message ? { reply, message } : { reply }),
    }),
  /** @param {string[][]} answers one array of selected labels per question */
  replyQuestion: (requestID, answers) =>
    _fetch(`question/${requestID}/reply`, {
      method: 'POST',
      body: JSON.stringify({ answers }),
    }),
  rejectQuestion: (requestID) =>
    _fetch(`question/${requestID}/reject`, { method: 'POST' }),

  // Files & Project
  /** opencode requires an explicit path; '.' means the project root. */
  listFiles: (path = '.') => _fetch(`file?path=${encodeURIComponent(path)}`),
  readFile: (path) => _fetch(`file/content?path=${encodeURIComponent(path)}`),
  getProject: () => _fetch('project'),

  // Agents, Providers & Config
  /** @returns {Array<{name: string, description?: string, mode: string, hidden?: boolean}>} */
  listAgents: () => _fetch('agent'),
  /** @returns {{all: Array, connected: string[], default: Object}} */
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

// Kept for callers that still reach for the old name.
client.getProviders = client.listProviders;

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
        onSessionSelect: (session) => {
          _chatView.loadSession(session);
          // On mobile the sidebar is a drawer; picking a session dismisses it.
          if (window.matchMedia('(max-width: 820px)').matches) {
            body.querySelector('.oc-sidebar')?.classList.remove('open');
            document.getElementById('opencode-sidebar-toggle')?.setAttribute('aria-expanded', 'false');
          }
        },
      });

      _projectPicker = ppMod.createProjectPicker(pickerEl, {
        onProjectChange: (dir) => {
          // Order matters: rebind the stream to the new directory before the
          // list refresh, so events for the incoming project aren't missed.
          setDirectory(dir);
          _chatView.loadSession(null);
          _sessionList.refresh();
        },
      });

      // Wire SSE events to UI components
      on('*', (evt) => {
        _sessionList?.handleEvent?.(evt);
        _chatView?.handleEvent?.(evt);
      });

      // The picker fires onProjectChange once it has loaded, but that never
      // happens when no projects are configured — refresh once so the sidebar
      // doesn't sit on its loading state forever.
      _sessionList.refresh();
    } catch (err) {
      console.error('[opencode] Failed to load UI modules:', err);
    }
  }

  // Connect SSE once the directory is known (the picker restores it above).
  _connectSSE();

  // Sidebar toggle (mobile: the sidebar is off-canvas below 820px)
  const sidebarToggle = document.getElementById('opencode-sidebar-toggle');
  if (sidebarToggle && !sidebarToggle._ocWired) {
    sidebarToggle._ocWired = true;
    sidebarToggle.addEventListener('click', () => {
      const sidebar = modal.querySelector('.oc-sidebar');
      if (!sidebar) return;
      const open = sidebar.classList.toggle('open');
      sidebarToggle.setAttribute('aria-expanded', String(open));
    });
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
