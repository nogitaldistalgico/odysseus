/**
 * @fileoverview Sidebar panel showing all opencode sessions for the selected project.
 *
 * Session objects come straight from opencode's `Session` schema: identity in
 * `id`/`title`, timestamps in `time.{created,updated}` (unix ms), and *no*
 * status field — liveness arrives separately via `session.status` events.
 */

/** @type {Record<string, {label: string, cls: string}>} */
const STATUS_META = {
    running: { label: 'Running', cls: 'running' },
    retry: { label: 'Retrying', cls: 'retry' },
    idle: { label: 'Idle', cls: 'idle' },
};

/**
 * Creates a session list component.
 * @param {HTMLElement} container - The DOM element to render the list into.
 * @param {Object} options - Configuration options.
 * @param {Object} options.client - The OpenCodeClient instance.
 * @param {Function} options.onSessionSelect - Callback when a session is selected.
 * @returns {Object} Session list API (refresh, handleEvent, destroy).
 */
export function createSessionList(container, { client, onSessionSelect }) {
    let activeSessionId = null;
    let sessions = [];
    /** sessionID -> SessionStatus.type, fed by the event stream. */
    const statuses = new Map();

    // UI Structure
    const wrapper = document.createElement('div');
    wrapper.className = 'oc-session-list-wrapper';

    const newBtn = document.createElement('button');
    newBtn.className = 'oc-new-session-btn';
    newBtn.innerHTML = '<span class="oc-plus" aria-hidden="true">+</span> New session';

    const listContainer = document.createElement('div');
    listContainer.className = 'oc-session-list';

    const emptyState = document.createElement('div');
    emptyState.className = 'oc-session-empty-state';
    emptyState.style.display = 'none';
    emptyState.textContent = 'No sessions yet.';

    const loadingState = document.createElement('div');
    loadingState.className = 'oc-session-loading-skeleton';
    loadingState.textContent = 'Loading…';

    wrapper.appendChild(newBtn);
    wrapper.appendChild(loadingState);
    wrapper.appendChild(listContainer);
    wrapper.appendChild(emptyState);
    container.appendChild(wrapper);

    /** opencode stores times as unix milliseconds under `time`. */
    const lastActivity = (s) => s?.time?.updated || s?.time?.created || 0;

    const relativeTime = (ms) => {
        if (!ms) return '';
        const diff = Date.now() - ms;
        const min = Math.round(diff / 60000);
        if (min < 1) return 'just now';
        if (min < 60) return `${min}m ago`;
        const hrs = Math.round(min / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.round(hrs / 24);
        if (days < 7) return `${days}d ago`;
        return new Date(ms).toLocaleDateString();
    };

    /**
     * Sort and render the sessions.
     */
    const renderList = () => {
        listContainer.innerHTML = '';

        if (sessions.length === 0) {
            listContainer.style.display = 'none';
            emptyState.style.display = 'block';
            return;
        }

        listContainer.style.display = 'block';
        emptyState.style.display = 'none';

        sessions.sort((a, b) => lastActivity(b) - lastActivity(a));

        sessions.forEach(session => {
            const item = document.createElement('div');
            item.className = 'oc-session-item';
            if (session.id === activeSessionId) item.classList.add('active');
            item.dataset.id = session.id;

            const row = document.createElement('div');
            row.className = 'oc-session-row';

            const status = statuses.get(session.id) || 'idle';
            const meta = STATUS_META[status] || STATUS_META.idle;
            const dot = document.createElement('span');
            dot.className = `oc-session-status ${meta.cls}`;
            dot.title = meta.label;

            // textContent, not innerHTML: session titles are model-generated,
            // so interpolating them into markup is an injection vector.
            const title = document.createElement('span');
            title.className = 'oc-session-title';
            title.textContent = session.title || 'Untitled';
            title.title = session.title || 'Untitled';

            const delBtn = document.createElement('button');
            delBtn.className = 'oc-session-delete';
            delBtn.type = 'button';
            delBtn.title = 'Delete session';
            delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>';

            row.appendChild(dot);
            row.appendChild(title);
            row.appendChild(delBtn);

            const time = document.createElement('div');
            time.className = 'oc-session-time';
            time.textContent = relativeTime(lastActivity(session));

            item.appendChild(row);
            item.appendChild(time);

            delBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (delBtn.dataset.confirm === 'true') {
                    try {
                        await client.deleteSession(session.id);
                        sessions = sessions.filter(s => s.id !== session.id);
                        statuses.delete(session.id);
                        if (activeSessionId === session.id) {
                            activeSessionId = null;
                            if (onSessionSelect) onSessionSelect(null);
                        }
                        renderList();
                    } catch (err) {
                        console.error('Error deleting session:', err);
                    }
                } else {
                    delBtn.dataset.confirm = 'true';
                    delBtn.textContent = 'Sure?';
                    delBtn.classList.add('confirming');
                    setTimeout(() => {
                        if (delBtn.isConnected) {
                            delBtn.dataset.confirm = 'false';
                            delBtn.classList.remove('confirming');
                            renderList();
                        }
                    }, 3000);
                }
            });

            item.addEventListener('click', () => {
                activeSessionId = session.id;
                renderList();
                if (onSessionSelect) onSessionSelect(session);
            });

            listContainer.appendChild(item);
        });
    };

    /**
     * Refresh the session list from the server.
     * The active project travels as a query param added by the client module.
     */
    const refresh = async () => {
        loadingState.style.display = 'block';
        listContainer.style.display = 'none';
        emptyState.style.display = 'none';

        try {
            const data = await client.listSessions();
            sessions = Array.isArray(data) ? data : (data?.sessions || []);
            loadingState.style.display = 'none';

            // Seed liveness once per refresh; events keep it current afterwards.
            try {
                const map = await client.getStatus();
                statuses.clear();
                Object.entries(map || {}).forEach(([id, st]) => statuses.set(id, st?.type || 'idle'));
            } catch { /* status is a nicety, not a requirement */ }

            renderList();
        } catch (err) {
            console.error('Failed to load sessions:', err);
            loadingState.style.display = 'none';
            emptyState.style.display = 'block';
            emptyState.textContent = 'Error loading sessions.';
        }
    };

    newBtn.addEventListener('click', async () => {
        try {
            newBtn.disabled = true;
            // No title: opencode names the session from the first prompt.
            const created = await client.createSession();
            const session = created?.info || created;
            if (session?.id) {
                sessions.push(session);
                activeSessionId = session.id;
                renderList();
                if (onSessionSelect) onSessionSelect(session);
            }
        } catch (err) {
            console.error('Failed to create session:', err);
        } finally {
            newBtn.disabled = false;
        }
    });

    return {
        refresh,

        /** @returns {string|null} */
        getActiveSessionId() {
            return activeSessionId;
        },

        /**
         * Apply one opencode event.
         *
         * Payloads follow packages/schema/src/v1/session.ts: session lifecycle
         * events carry `{sessionID, info}` (the session lives in `info`, not at
         * the top level) and liveness arrives as `session.status`/`session.idle`.
         * @param {{type: string, properties: Object}} event
         */
        handleEvent(event) {
            if (!event || !event.type) return;
            const p = event.properties || {};

            switch (event.type) {
                case 'session.created':
                case 'session.updated': {
                    const info = p.info;
                    if (!info?.id) return;
                    const idx = sessions.findIndex(s => s.id === info.id);
                    if (idx === -1) sessions.push(info);
                    else sessions[idx] = { ...sessions[idx], ...info };
                    renderList();
                    break;
                }
                case 'session.deleted': {
                    const id = p.sessionID || p.info?.id;
                    if (!id) return;
                    sessions = sessions.filter(s => s.id !== id);
                    statuses.delete(id);
                    if (activeSessionId === id) {
                        activeSessionId = null;
                        if (onSessionSelect) onSessionSelect(null);
                    }
                    renderList();
                    break;
                }
                case 'session.status': {
                    if (!p.sessionID) return;
                    statuses.set(p.sessionID, p.status?.type || 'idle');
                    renderList();
                    break;
                }
                case 'session.idle': {
                    if (!p.sessionID) return;
                    statuses.set(p.sessionID, 'idle');
                    renderList();
                    break;
                }
                default:
                    break;
            }
        },

        /**
         * Clean up DOM elements.
         */
        destroy() {
            wrapper.remove();
        }
    };
}
