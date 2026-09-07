/**
 * @fileoverview Main chat interface for interacting with an OpenCode session.
 */

import { renderToolPart, renderToolCall, renderToolResult, updateToolResult } from './toolRenderer.js';
import { renderCodeBlock, renderDiff } from './codeRenderer.js';
import { renderPermission, renderQuestion, removePermission, removeQuestion } from './permissionUI.js';

/**
 * Very lightweight and simplistic markdown parser.
 * Supports bold, italic, inline code, headings, lists, blockquotes, links, and codeblocks (via delegation).
 * @param {string} text 
 * @returns {HTMLElement} Parsed markdown wrapper
 */
function parseMarkdown(text) {
    const container = document.createElement('div');
    container.className = 'markdown-body';
    
    // Split block elements simply by double newline (primitive but ok for basic chat)
    const blocks = text.split(/\n{2,}/);
    
    blocks.forEach(block => {
        let htmlBlock = block;
        let isSpecial = false;

        // Check for code block
        const codeBlockMatch = block.match(/^```(\w+)?\n([\s\S]*?)```$/);
        if (codeBlockMatch) {
            const lang = codeBlockMatch[1] || '';
            const code = codeBlockMatch[2];
            container.appendChild(renderCodeBlock(code, lang));
            return;
        }

        // Check for headings
        const headerMatch = block.match(/^(#{1,6})\s+(.*)$/);
        if (headerMatch) {
            const level = headerMatch[1].length;
            const h = document.createElement(`h${level}`);
            h.innerHTML = parseInline(headerMatch[2]);
            container.appendChild(h);
            return;
        }

        // Check for unordered lists
        if (/^(\s*-\s+.*(?:\n|$))+/.test(block)) {
            const ul = document.createElement('ul');
            const items = block.split('\n');
            items.forEach(item => {
                const textMatch = item.match(/^\s*-\s+(.*)$/);
                if (textMatch) {
                    const li = document.createElement('li');
                    li.innerHTML = parseInline(textMatch[1]);
                    ul.appendChild(li);
                }
            });
            container.appendChild(ul);
            return;
        }

        // Check for ordered lists
        if (/^(\s*\d+\.\s+.*(?:\n|$))+/.test(block)) {
            const ol = document.createElement('ol');
            const items = block.split('\n');
            items.forEach(item => {
                const textMatch = item.match(/^\s*\d+\.\s+(.*)$/);
                if (textMatch) {
                    const li = document.createElement('li');
                    li.innerHTML = parseInline(textMatch[1]);
                    ol.appendChild(li);
                }
            });
            container.appendChild(ol);
            return;
        }

        // Blockquotes
        if (/^(\s*>\s+.*(?:\n|$))+/.test(block)) {
            const quote = document.createElement('blockquote');
            // Remove > prefixes
            const text = block.replace(/^\s*>\s+/gm, '');
            quote.innerHTML = parseInline(text);
            container.appendChild(quote);
            return;
        }

        // Default Paragraph
        const p = document.createElement('p');
        p.innerHTML = parseInline(block);
        container.appendChild(p);
    });

    return container;
}

/**
 * Parses inline markdown elements
 * @param {string} text 
 * @returns {string} HTML string
 */
function parseInline(text) {
    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    
    // Bold
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    // Inline code
    html = html.replace(/`(.*?)`/g, '<code>$1</code>');
    // Links
    html = html.replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    
    return html;
}


/**
 * Creates the chat view component.
 * @param {HTMLElement} container - The DOM element to render the view into.
 * @param {Object} options - Configuration options.
 * @param {Object} options.client - The OpenCodeClient instance.
 * @returns {Object} Chat view API.
 */
export function createChatView(container, { client }) {
    let activeSession = null;
    let messageElements = new Map(); // id -> HTMLElement
    let partElements = new Map(); // part_id -> HTMLElement
    let isUserScrolledUp = false;
    // opencode's revert endpoint needs a messageID; the last user turn is the
    // only anchor a "revert" button can sensibly mean.
    let lastUserMessageID = null;
    // partID -> the last full Part object, so message.part.delta can append to
    // the underlying text and re-render rather than mutating rendered markup.
    const partData = new Map();

    // Container wrapper
    const wrapper = document.createElement('div');
    wrapper.style.display = 'flex';
    wrapper.style.flexDirection = 'column';
    wrapper.style.flex = '1';
    wrapper.style.minHeight = '0';
    wrapper.style.overflow = 'hidden';

    // Empty state
    const emptyState = document.createElement('div');
    emptyState.className = 'oc-empty-state';
    emptyState.textContent = 'Select a session or create a new one';

    // Main Chat Interface
    const chatInterface = document.createElement('div');
    chatInterface.style.display = 'none';
    chatInterface.style.flexDirection = 'column';
    chatInterface.style.flex = '1';
    chatInterface.style.minHeight = '0';
    chatInterface.style.overflow = 'hidden';

    // Header
    const header = document.createElement('div');
    header.className = 'oc-session-info';
    const headerTitle = document.createElement('span');
    headerTitle.className = 'oc-session-info-title';

    const headerStatus = document.createElement('span');
    headerStatus.className = 'oc-session-status-chip';

    const errorBar = document.createElement('div');
    errorBar.className = 'oc-error-bar';
    errorBar.setAttribute('role', 'status');
    
    const headerActions = document.createElement('div');
    headerActions.className = 'oc-session-info-actions';

    const diffBtn = document.createElement('button');
    diffBtn.textContent = 'View Diff';
    
    const todoBtn = document.createElement('button');
    todoBtn.textContent = 'View Todo';

    const revertBtn = document.createElement('button');
    revertBtn.textContent = 'Revert';

    headerActions.appendChild(diffBtn);
    headerActions.appendChild(todoBtn);
    headerActions.appendChild(revertBtn);

    header.appendChild(headerTitle);
    header.appendChild(headerStatus);
    header.appendChild(headerActions);

    // Messages Area
    const messagesArea = document.createElement('div');
    messagesArea.className = 'oc-chat-messages';

    // Prompt Bar
    const promptBar = document.createElement('div');
    promptBar.className = 'oc-prompt-bar';

    // Agents are discovered from opencode (GET /agent) rather than hardcoded:
    // the built-ins are "build" and "plan", and users can define their own.
    const modeSelect = document.createElement('select');
    modeSelect.className = 'oc-mode-toggle';
    modeSelect.title = 'Agent';
    const buildOpt = document.createElement('option'); buildOpt.value = 'build'; buildOpt.textContent = 'Build';
    const planOpt = document.createElement('option'); planOpt.value = 'plan'; planOpt.textContent = 'Plan';
    modeSelect.appendChild(buildOpt);
    modeSelect.appendChild(planOpt);

    const modelSelect = document.createElement('select');
    modelSelect.className = 'oc-mode-toggle';
    modelSelect.title = 'Select Model';
    const defaultModelOpt = document.createElement('option'); defaultModelOpt.value = ''; defaultModelOpt.textContent = 'Default Model';
    modelSelect.appendChild(defaultModelOpt);

    const input = document.createElement('textarea');
    input.className = 'oc-prompt-input';
    input.placeholder = 'Type your message... (Enter to send, Shift+Enter for newline)';
    input.rows = 2;

    const btnWrapper = document.createElement('div');
    btnWrapper.className = 'oc-btn-wrapper';

    const sendBtn = document.createElement('button');
    sendBtn.className = 'oc-send-btn';
    sendBtn.textContent = 'Send';

    const abortBtn = document.createElement('button');
    abortBtn.className = 'oc-abort-btn';
    abortBtn.textContent = 'Abort';
    abortBtn.style.display = 'none';

    btnWrapper.appendChild(sendBtn);
    btnWrapper.appendChild(abortBtn);

    const inputContainer = document.createElement('div');
    inputContainer.className = 'oc-prompt-input-container';

    // Agent + model sit on their own row above the text field, so the
    // composer reads as one control instead of a cramped left column.
    const selectorsWrap = document.createElement('div');
    selectorsWrap.className = 'oc-composer-tools';
    selectorsWrap.appendChild(modeSelect);
    selectorsWrap.appendChild(modelSelect);

    const composerRow = document.createElement('div');
    composerRow.className = 'oc-composer-row';
    composerRow.appendChild(input);
    composerRow.appendChild(btnWrapper);

    inputContainer.appendChild(selectorsWrap);
    inputContainer.appendChild(composerRow);

    promptBar.appendChild(inputContainer);

    chatInterface.appendChild(header);
    chatInterface.appendChild(errorBar);
    chatInterface.appendChild(messagesArea);
    chatInterface.appendChild(promptBar);
    
    wrapper.appendChild(emptyState);
    wrapper.appendChild(chatInterface);
    container.appendChild(wrapper);

    // Populate the agent list from the server, keeping build/plan as fallback.
    (async () => {
        try {
            const agents = await client.listAgents();
            const primary = (Array.isArray(agents) ? agents : [])
                .filter(a => a && a.name && a.mode !== 'subagent' && a.hidden !== true);
            if (primary.length) {
                const previous = modeSelect.value;
                modeSelect.innerHTML = '';
                for (const a of primary) {
                    const opt = document.createElement('option');
                    opt.value = a.name;
                    opt.textContent = a.name.charAt(0).toUpperCase() + a.name.slice(1);
                    if (a.description) opt.title = a.description;
                    modeSelect.appendChild(opt);
                }
                if (primary.some(a => a.name === previous)) modeSelect.value = previous;
            }
        } catch (err) {
            console.warn('[opencode] agent list unavailable, using defaults:', err);
        }
    })();

    // Fetch and populate models
    (async () => {
        try {
            if (client.listProviders) {
                const providersRaw = await client.listProviders();
                let providerList = [];
                
                if (providersRaw && providersRaw.all && Array.isArray(providersRaw.connected)) {
                    // OpenCode V2: only show models for providers the user has actually connected
                    const connectedIds = providersRaw.connected;
                    providerList = providersRaw.all.filter(p => connectedIds.includes(p.id));
                } else {
                    providerList = providersRaw.all || providersRaw.providers || providersRaw.data || (Array.isArray(providersRaw) ? providersRaw : Object.values(providersRaw)) || [];
                }
                
                let foundAny = false;
                for (const p of providerList) {
                    if (!p || (!p.id && !p.name)) continue;
                    
                    // OpenCode v2 embeds models inside the provider object
                    const modelsMap = p.models || {};
                    const modelList = Array.isArray(modelsMap) ? modelsMap : Object.values(modelsMap);
                    
                    if (modelList.length === 0) continue;
                    
                    const group = document.createElement('optgroup');
                    group.label = p.name || p.id;
                    for (const m of modelList) {
                        if (!m || (!m.id && !m.name)) continue;
                        const opt = document.createElement('option');
                        opt.value = JSON.stringify({ providerID: p.id || p.name, modelID: m.id || m.name });
                        opt.textContent = m.name || m.id;
                        group.appendChild(opt);
                        foundAny = true;
                    }
                    modelSelect.appendChild(group);
                }
                
                if (!foundAny) {
                    defaultModelOpt.textContent = 'Default (No models found)';
                } else {
                    defaultModelOpt.textContent = 'Default Model';
                }
            }
        } catch (err) {
            console.error('Failed to load opencode models:', err);
            defaultModelOpt.textContent = 'Default (Load Error)';
        }
    })();

    // Auto-scroll logic
    messagesArea.addEventListener('scroll', () => {
        const isAtBottom = messagesArea.scrollHeight - messagesArea.scrollTop <= messagesArea.clientHeight + 10;
        isUserScrolledUp = !isAtBottom;
    });

    const scrollToBottom = () => {
        if (!isUserScrolledUp) {
            messagesArea.scrollTop = messagesArea.scrollHeight;
        }
    };

    /**
     * Set session state (busy/idle)
     */
    const setSessionState = (status) => {
        // SessionStatus.type is "idle" | "running" | "retry".
        const isBusy = status === 'running' || status === 'retry';
        input.disabled = isBusy;
        sendBtn.disabled = isBusy;
        abortBtn.style.display = isBusy ? 'inline-flex' : 'none';
        sendBtn.style.display = isBusy ? 'none' : 'inline-flex';
        if (headerTitle) headerTitle.textContent = activeSession?.title || 'Untitled';
        if (headerStatus) {
            headerStatus.textContent = isBusy ? (status === 'retry' ? 'retrying' : 'working…') : '';
            headerStatus.dataset.state = isBusy ? 'busy' : 'idle';
        }
    };

    /** Non-blocking inline error banner (alert() froze the whole view). */
    const showError = (msg) => {
        errorBar.textContent = msg;
        errorBar.classList.add('visible');
        clearTimeout(showError._t);
        showError._t = setTimeout(() => errorBar.classList.remove('visible'), 6000);
    };

    // Actions
    const handleSend = async () => {
        if (!activeSession || !input.value.trim()) return;
        const text = input.value.trim();
        const mode = modeSelect.value;
        const modelStr = modelSelect.value;
        let modelObj = undefined;
        if (modelStr) {
            try { modelObj = JSON.parse(modelStr); } catch (e) {}
        }
        
        input.value = '';
        setSessionState('running');

        try {
            // prompt_async returns 204 immediately; the assistant's reply
            // arrives incrementally over the event stream. The old synchronous
            // POST /message blocked the UI for the whole generation instead.
            await client.promptAsync(activeSession.id, text, mode, modelObj);
        } catch (err) {
            console.error('Failed to send message:', err);
            input.value = text;
            setSessionState('idle');
            showError(`Failed to send message: ${err.message}`);
        }
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    sendBtn.addEventListener('click', handleSend);

    abortBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        try {
            await client.abort(activeSession.id);
        } catch(e) {
            console.error('Abort failed', e);
            showError(`Abort failed: ${e.message}`);
        }
    });

    /** Minimal side panel used for diff and todo output. */
    const openPanel = (heading, buildBody) => {
        const panel = document.createElement('div');
        // `oc-scope` carries the view's design tokens and the specificity boost
        // that beats style.css's global element rules; the panel mounts on
        // document.body, outside #opencode-modal, so it needs its own copy.
        panel.className = 'oc-panel oc-scope';
        const card = document.createElement('div');
        card.className = 'oc-panel-card';
        const head = document.createElement('div');
        head.className = 'oc-panel-head';
        const h = document.createElement('h3');
        h.textContent = heading;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'oc-panel-close';
        close.setAttribute('aria-label', 'Close');
        close.textContent = '✕';
        head.appendChild(h);
        head.appendChild(close);
        const body = document.createElement('div');
        body.className = 'oc-panel-body';
        buildBody(body);
        card.appendChild(head);
        card.appendChild(body);
        panel.appendChild(card);
        document.body.appendChild(panel);
        const dismiss = () => panel.remove();
        close.addEventListener('click', dismiss);
        panel.addEventListener('click', (e) => { if (e.target === panel) dismiss(); });
        return panel;
    };

    diffBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        try {
            // GET /session/:id/diff answers with FileDiff.Info[], not a string.
            const diffs = await client.getDiff(activeSession.id);
            const list = Array.isArray(diffs) ? diffs : (diffs?.diff || []);
            openPanel('Changes', (body) => {
                if (!list.length) {
                    const p = document.createElement('p');
                    p.className = 'oc-panel-empty';
                    p.textContent = 'No changes in this session.';
                    body.appendChild(p);
                    return;
                }
                for (const d of list) {
                    const file = document.createElement('div');
                    file.className = 'oc-diff-file';
                    const name = document.createElement('div');
                    name.className = 'oc-diff-file-name';
                    name.textContent = d.file || d.path || 'file';
                    file.appendChild(name);
                    const patch = d.patch || d.diff || '';
                    file.appendChild(renderDiff(typeof patch === 'string' ? patch : JSON.stringify(patch, null, 2)));
                    body.appendChild(file);
                }
            });
        } catch(e) {
            console.error('Diff error', e);
            showError(`Could not load changes: ${e.message}`);
        }
    });

    todoBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        try {
            const data = await client.getTodo(activeSession.id);
            // Todo entries are objects: { content, status, priority }.
            const todos = Array.isArray(data) ? data : (data?.todos || []);
            openPanel('Todo', (body) => {
                if (!todos.length) {
                    const p = document.createElement('p');
                    p.className = 'oc-panel-empty';
                    p.textContent = 'No todos yet.';
                    body.appendChild(p);
                    return;
                }
                const ul = document.createElement('ul');
                ul.className = 'oc-todo-list';
                for (const t of todos) {
                    const li = document.createElement('li');
                    li.className = `oc-todo-item ${t.status || ''}`;
                    li.textContent = typeof t === 'string' ? t : (t.content || '');
                    ul.appendChild(li);
                }
                body.appendChild(ul);
            });
        } catch(e) {
            console.error('Todo error', e);
            showError(`Could not load todos: ${e.message}`);
        }
    });

    revertBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        // opencode reverts *to a message*: the messageID is required, so we
        // undo everything from the last user turn onwards.
        if (!lastUserMessageID) {
            showError('Nothing to revert yet.');
            return;
        }
        if (!confirm('Revert the changes made since your last message?')) return;
        try {
            await client.revert(activeSession.id, lastUserMessageID);
        } catch(e) {
            console.error('Revert error', e);
            showError(`Revert failed: ${e.message}`);
        }
    });

    // Rendering Helpers
    const createPartElement = (part) => {
        const div = document.createElement('div');
        div.className = 'oc-message-part';
        if (part.id) div.dataset.id = part.id;
        div.dataset.type = part.type || '';
        
        // OpenCode part types (from message-v2.ts):
        //   "text", "tool", "reasoning", "step-start", "step-finish",
        //   "file", "snapshot", "patch", "compaction", "subtask", "agent", "retry"
        
        if (part.type === 'text') {
            if (part.text) div.appendChild(parseMarkdown(part.text));
            
        } else if (part.type === 'tool') {
            // This is THE main tool part type in OpenCode.
            // It has part.tool (name), part.state (status, input, output, metadata)
            div.appendChild(renderToolPart(part, activeSession?.directory));
            
        } else if (part.type === 'reasoning') {
            const details = document.createElement('details');
            details.className = 'oc-thinking-block';
            const summary = document.createElement('summary');
            summary.textContent = 'Thinking...';
            const content = document.createElement('div');
            content.className = 'content';
            if (part.text) content.appendChild(parseMarkdown(part.text));
            details.appendChild(summary);
            details.appendChild(content);
            div.appendChild(details);
            
        } else if (part.type === 'step-start') {
            // Model/provider step indicator - skip or render minimally
            // OpenCode shows this as the provider icon in the left margin
            div.style.display = 'none';
            
        } else if (part.type === 'step-finish') {
            // Token/cost summary at end of a step - skip for now
            div.style.display = 'none';
            
        } else if (part.type === 'file') {
            // User attachment
            const attachDiv = document.createElement('div');
            attachDiv.className = 'oc-attachment';
            attachDiv.textContent = `📎 ${part.filename || 'Datei'}`;
            div.appendChild(attachDiv);
            
        } else if (part.type === 'snapshot' || part.type === 'patch') {
            // Internal bookkeeping, don't render
            div.style.display = 'none';
            
        } else if (part.type === 'compaction') {
            // Context compaction marker
            div.style.display = 'none';
            
        } else if (part.type === 'agent') {
            // Sub-agent part
            const agentDiv = document.createElement('div');
            agentDiv.className = 'oc-agent-part';
            agentDiv.textContent = `🤖 Agent: ${part.name || ''}`;
            div.appendChild(agentDiv);
            
        } else if (part.type === 'retry') {
            const retryDiv = document.createElement('div');
            retryDiv.className = 'oc-retry-part';
            retryDiv.textContent = `🔄 Retry #${part.attempt || ''}`;
            div.appendChild(retryDiv);
            
        } else if (part.type === 'tool_call' || part.type === 'tool_result') {
            // Legacy/v1 format fallback
            div.appendChild(renderToolPart(part, activeSession?.directory));
            
        } else {
            console.warn('Odysseus: Unrecognized message part type:', part.type, JSON.stringify(part).substring(0, 200));
        }
        return div;
    };

    const renderMessage = (msg) => {
        // OpenCode v2 messages come as { info: {...}, parts: [...] }
        // Flatten if needed
        const info = msg.info || msg;
        const parts = msg.parts || info.parts || [];
        
        const msgEl = document.createElement('div');
        msgEl.className = `oc-message ${info.role || 'assistant'}`;
        msgEl.dataset.id = info.id;

        const contentWrap = document.createElement('div');
        contentWrap.className = 'oc-message-content';

        // Filter out internal/invisible parts (like OpenCode does in Share.tsx)
        const visibleParts = parts.filter((p, index) => {
            if (p.type === 'step-start' && index > 0) return false;
            if (p.type === 'snapshot') return false;
            if (p.type === 'patch') return false;
            if (p.type === 'step-finish') return false;
            if (p.type === 'compaction') return false;
            if (p.type === 'text' && p.synthetic === true) return false;
            if (p.type === 'text' && !p.text) return false;
            if (p.type === 'tool' && p.state?.status === 'pending') return false;
            return true;
        });

        visibleParts.forEach(part => {
            const pEl = createPartElement(part);
            if (part.id) {
                partElements.set(part.id, pEl);
                partData.set(part.id, part);
            }
            contentWrap.appendChild(pEl);
        });

        msgEl.appendChild(contentWrap);
        messagesArea.appendChild(msgEl);
        messageElements.set(info.id, msgEl);
        scrollToBottom();
    };

    const loadSession = async (session) => {
        activeSession = session;
        if (!session) {
            emptyState.style.display = 'flex';
            chatInterface.style.display = 'none';
            return;
        }

        emptyState.style.display = 'none';
        chatInterface.style.display = 'flex';
        headerTitle.textContent = session.title || 'Untitled';
        messagesArea.innerHTML = '';
        messageElements.clear();
        partElements.clear();
        partData.clear();
        lastUserMessageID = null;
        setSessionState('idle');
        isUserScrolledUp = false;

        try {
            // GET /session/:id/message answers with [{ info, parts }].
            const data = await client.getMessages(session.id);
            const messages = Array.isArray(data) ? data : (data?.items || data?.messages || []);
            messages.forEach(renderMessage);
            for (let i = messages.length - 1; i >= 0; i--) {
                const info = messages[i]?.info || messages[i];
                if (info?.role === 'user') { lastUserMessageID = info.id; break; }
            }
        } catch (err) {
            console.error('Failed to load messages:', err);
            showError(`Could not load messages: ${err.message}`);
        }
    };


    return {
        loadSession,
        getActiveSession: () => activeSession,
        /**
         * Apply one opencode event.
         *
         * Contract (packages/schema/src/v1/session.ts + openapi.json):
         *   session.updated       { sessionID, info }
         *   session.status        { sessionID, status: {type} }
         *   session.idle          { sessionID }
         *   session.error         { sessionID, error }
         *   message.updated       { sessionID, info }
         *   message.removed       { sessionID, messageID }
         *   message.part.updated  { sessionID, part, time }
         *   message.part.delta    { sessionID, messageID, partID, field, delta }
         *   message.part.removed  { sessionID, messageID, partID }
         *   permission.asked      { id, sessionID, permission, patterns, metadata, always }
         *   question.asked        { id, sessionID, questions }
         *
         * Note the capital-ID spelling throughout, and that entities live under
         * `info`/`part` rather than at the top level.
         *
         * @param {{type: string, properties: Object}} event
         */
        handleEvent(event) {
            if (!event || !event.type) return;
            const p = event.properties || {};

            // Everything below is session-scoped; ignore other sessions.
            if (p.sessionID && p.sessionID !== activeSession?.id) return;
            if (!activeSession) return;

            switch (event.type) {
                case 'session.updated': {
                    if (p.info) {
                        activeSession = { ...activeSession, ...p.info };
                        if (headerTitle) headerTitle.textContent = activeSession.title || 'Untitled';
                    }
                    break;
                }
                case 'session.status': {
                    setSessionState(p.status?.type || 'idle');
                    break;
                }
                case 'session.idle': {
                    setSessionState('idle');
                    break;
                }
                case 'session.error': {
                    setSessionState('idle');
                    const err = p.error;
                    showError(err?.data?.message || err?.name || 'The session reported an error.');
                    break;
                }
                case 'message.updated': {
                    const info = p.info;
                    if (!info?.id) break;
                    if (info.role === 'user') lastUserMessageID = info.id;
                    const existing = messageElements.get(info.id);
                    if (existing) {
                        // Only the envelope changed; parts arrive separately.
                        existing.className = `oc-message ${info.role || 'assistant'}`;
                    } else {
                        renderMessage({ info, parts: [] });
                    }
                    break;
                }
                case 'message.removed': {
                    messageElements.get(p.messageID)?.remove();
                    messageElements.delete(p.messageID);
                    break;
                }
                case 'message.part.updated': {
                    const part = p.part;
                    if (!part?.id) break;
                    partData.set(part.id, part);
                    const existing = partElements.get(part.id);
                    const el = createPartElement(part);
                    partElements.set(part.id, el);
                    if (existing) {
                        existing.replaceWith(el);
                    } else {
                        // First sighting: attach it to its message, creating a
                        // placeholder if the part outran message.updated.
                        let msgEl = messageElements.get(part.messageID);
                        if (!msgEl) {
                            renderMessage({ info: { id: part.messageID, role: 'assistant' }, parts: [] });
                            msgEl = messageElements.get(part.messageID);
                        }
                        msgEl?.querySelector('.oc-message-content')?.appendChild(el);
                    }
                    scrollToBottom();
                    break;
                }
                case 'message.part.delta': {
                    // `field` names the property being appended to (text,
                    // reasoning input, ...). Re-render from the accumulated
                    // value so markdown stays parsed mid-stream.
                    if (typeof p.delta !== 'string') break;
                    const cached = partData.get(p.partID);
                    const el = partElements.get(p.partID);
                    if (!cached || !el) break;
                    const field = p.field || 'text';
                    cached[field] = (cached[field] || '') + p.delta;
                    const fresh = createPartElement(cached);
                    partElements.set(p.partID, fresh);
                    el.replaceWith(fresh);
                    scrollToBottom();
                    break;
                }
                case 'message.part.removed': {
                    partElements.get(p.partID)?.remove();
                    partElements.delete(p.partID);
                    partData.delete(p.partID);
                    break;
                }
                case 'permission.asked': {
                    const card = renderPermission(p, async (requestID, reply) => {
                        try {
                            await client.replyPermission(requestID, reply);
                        } catch (err) {
                            showError(`Permission reply failed: ${err.message}`);
                        }
                        removePermission(requestID);
                    });
                    messagesArea.appendChild(card);
                    scrollToBottom();
                    break;
                }
                case 'permission.replied': {
                    removePermission(p.requestID);
                    break;
                }
                case 'question.asked': {
                    const card = renderQuestion(
                        p,
                        async (requestID, answers) => {
                            try {
                                await client.replyQuestion(requestID, answers);
                            } catch (err) {
                                showError(`Answer failed: ${err.message}`);
                            }
                            removeQuestion(requestID);
                        },
                        async (requestID) => {
                            try {
                                await client.rejectQuestion(requestID);
                            } catch (err) {
                                showError(`Skip failed: ${err.message}`);
                            }
                            removeQuestion(requestID);
                        },
                    );
                    messagesArea.appendChild(card);
                    scrollToBottom();
                    break;
                }
                case 'question.replied':
                case 'question.rejected': {
                    removeQuestion(p.requestID);
                    break;
                }
                default:
                    break;
            }
        },
        destroy() {
            wrapper.remove();
        }
    };
}
