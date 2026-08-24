/**
 * @fileoverview Sidebar panel showing all opencode sessions for the selected project.
 */

/**
 * Creates a session list component.
 * @param {HTMLElement} container - The DOM element to render the list into.
 * @param {Object} options - Configuration options.
 * @param {Object} options.client - The OpenCodeClient instance.
 * @param {Function} options.onSessionSelect - Callback when a session is selected.
 * @returns {Object} Session list API (refresh, setDirectory, handleEvent, destroy).
 */
export function createSessionList(container, { client, onSessionSelect }) {
    let currentDirectory = null;
    let activeSessionId = null;
    let sessions = [];

    // UI Structure
    const wrapper = document.createElement('div');
    wrapper.className = 'oc-session-list-wrapper';

    const newBtn = document.createElement('button');
    newBtn.className = 'oc-new-session-btn';
    newBtn.textContent = 'New Session';
    
    const listContainer = document.createElement('div');
    listContainer.className = 'oc-session-list';

    const emptyState = document.createElement('div');
    emptyState.className = 'oc-session-empty-state';
    emptyState.style.display = 'none';
    emptyState.textContent = 'No sessions found.';

    const loadingState = document.createElement('div');
    loadingState.className = 'oc-session-loading-skeleton';
    loadingState.textContent = 'Loading...';

    wrapper.appendChild(newBtn);
    wrapper.appendChild(loadingState);
    wrapper.appendChild(listContainer);
    wrapper.appendChild(emptyState);
    container.appendChild(wrapper);

    /**
     * Get the badge style based on status.
     * @param {string} status 
     * @returns {string} HTML for the badge
     */
    const getStatusBadge = (status) => {
        let symbol = '🟢';
        let cls = 'idle';
        if (status === 'busy') {
            symbol = '🔵';
            cls = 'busy'; // CSS can use this class to pulse
        } else if (status === 'error') {
            symbol = '🔴';
            cls = 'error';
        }
        return `<span class="oc-session-status ${cls}" title="${status}">${symbol}</span>`;
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

        // Sort by last activity (most recent first)
        sessions.sort((a, b) => new Date(b.updatedAt || b.createdAt) - new Date(a.updatedAt || a.createdAt));

        sessions.forEach(session => {
            const item = document.createElement('div');
            item.className = 'oc-session-item';
            if (session.id === activeSessionId) {
                item.classList.add('active');
            }
            item.dataset.id = session.id;

            const title = session.title || 'Untitled';
            const dateStr = session.updatedAt || session.createdAt || session.created_at || session.updated_at;
            const time = dateStr ? new Date(dateStr).toLocaleString() : 'Just now';
            
            item.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; width:100%; gap:8px;">
                    <div style="display:flex; align-items:center; gap:8px; overflow:hidden;">
                        ${getStatusBadge(session.status)}
                        <span class="oc-session-title" title="${title}">${title}</span>
                    </div>
                </div>
                <div style="font-size:0.75em; opacity:0.6; margin-top:4px;">${time}</div>
            `;

            const delBtn = document.createElement('button');
            delBtn.className = 'oc-session-delete';
            delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"></path></svg>';
            delBtn.style.cssText = 'background:transparent; border:none; color:var(--fg); opacity:0.5; cursor:pointer; padding:4px; margin-left:auto; display:flex;';
            delBtn.title = 'Delete Session';
            
            item.querySelector('div').appendChild(delBtn);
            
            delBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (delBtn.dataset.confirm === 'true') {
                    // Confirmed delete
                    try {
                        if (client.deleteSession) {
                            await client.deleteSession(session.id);
                        } else {
                            await fetch(`/api/opencode/session/${session.id}`, { method: 'DELETE' });
                        }
                        sessions = sessions.filter(s => s.id !== session.id);
                        if (activeSessionId === session.id) {
                            activeSessionId = null;
                            if (onSessionSelect) onSessionSelect(null);
                        }
                        renderList();
                    } catch (err) {
                        console.error('Error deleting session:', err);
                    }
                } else {
                    // Show confirmation inline
                    delBtn.dataset.confirm = 'true';
                    delBtn.textContent = 'Sure?';
                    delBtn.classList.add('confirming');
                    
                    // Reset after 3 seconds
                    setTimeout(() => {
                        if (item.contains(delBtn)) {
                            delBtn.dataset.confirm = 'false';
                            delBtn.textContent = '🗑️';
                            delBtn.classList.remove('confirming');
                        }
                    }, 3000);
                }
            });

            item.appendChild(delBtn);

            item.addEventListener('click', () => {
                activeSessionId = session.id;
                renderList();
                if (onSessionSelect) {
                    onSessionSelect(session);
                }
            });

            listContainer.appendChild(item);
        });
    };

    /**
     * Refresh the session list from the server.
     */
    const refresh = async () => {
        if (!currentDirectory) return;
        
        loadingState.style.display = 'block';
        listContainer.style.display = 'none';
        emptyState.style.display = 'none';

        try {
            let data;
            if (client.listSessions) {
                data = await client.listSessions(currentDirectory);
            } else {
                const res = await fetch(`/api/opencode/session?directory=${encodeURIComponent(currentDirectory)}`);
                if (!res.ok) throw new Error(`HTTP error ${res.status}`);
                data = await res.json();
            }
            sessions = Array.isArray(data) ? data : (data.sessions || []);
            loadingState.style.display = 'none';
            renderList();
        } catch (err) {
            console.error('Failed to load sessions:', err);
            loadingState.style.display = 'none';
            emptyState.style.display = 'block';
            emptyState.textContent = 'Error loading sessions.';
        }
    };

    // Event Listeners
    newBtn.addEventListener('click', async () => {
        if (!currentDirectory) return;
        
        try {
            newBtn.disabled = true;
            let newSession;
            if (client.createSession) {
                newSession = await client.createSession(currentDirectory);
            } else {
                const res = await fetch('/api/opencode/session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ directory: currentDirectory })
                });
                if (!res.ok) throw new Error(`HTTP error ${res.status}`);
                newSession = await res.json();
            }
            sessions.push(newSession);
            activeSessionId = newSession.id;
            renderList();
            if (onSessionSelect) {
                onSessionSelect(newSession);
            }
        } catch (err) {
            console.error('Failed to create session:', err);
        } finally {
            newBtn.disabled = false;
        }
    });

    return {
        /**
         * Refresh the session list.
         */
        refresh,
        /**
         * Set the active directory and reload sessions.
         * @param {string} dir 
         */
        setDirectory(dir) {
            currentDirectory = dir;
            activeSessionId = null;
            refresh();
        },
        /**
         * Handle SSE events to update the list live.
         * @param {Object} event 
         */
        handleEvent(event) {
            if (!event || !event.type || !event.data) return;
            const data = event.data;

            if (event.type === 'session.created') {
                if (data.directory === currentDirectory && !sessions.find(s => s.id === data.id)) {
                    sessions.push(data);
                    renderList();
                }
            } else if (event.type === 'session.updated' || event.type === 'session.status') {
                const idx = sessions.findIndex(s => s.id === data.id);
                if (idx !== -1) {
                    sessions[idx] = { ...sessions[idx], ...data };
                    renderList();
                }
            } else if (event.type === 'session.deleted') {
                sessions = sessions.filter(s => s.id !== data.id);
                if (activeSessionId === data.id) {
                    activeSessionId = null;
                    if (onSessionSelect) onSessionSelect(null);
                }
                renderList();
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
