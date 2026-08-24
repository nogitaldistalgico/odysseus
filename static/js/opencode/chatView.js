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
            const codeElem = renderCodeBlock ? renderCodeBlock(code, lang) : document.createTextNode(`Code: ${code}`);
            container.appendChild(codeElem);
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
    header.appendChild(headerActions);

    // Messages Area
    const messagesArea = document.createElement('div');
    messagesArea.className = 'oc-chat-messages';

    // Prompt Bar
    const promptBar = document.createElement('div');
    promptBar.className = 'oc-prompt-bar';

    const modeSelect = document.createElement('select');
    modeSelect.className = 'oc-mode-toggle';
    const planOpt = document.createElement('option'); planOpt.value = 'plan'; planOpt.textContent = 'Plan';
    const codeOpt = document.createElement('option'); codeOpt.value = 'coder'; codeOpt.textContent = 'Coder';
    modeSelect.appendChild(planOpt);
    modeSelect.appendChild(codeOpt);

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

    // Group selectors together
    const selectorsWrap = document.createElement('div');
    selectorsWrap.style.display = 'flex';
    selectorsWrap.style.flexDirection = 'column';
    selectorsWrap.style.gap = '5px';
    selectorsWrap.appendChild(modeSelect);
    selectorsWrap.appendChild(modelSelect);

    inputContainer.appendChild(selectorsWrap);
    inputContainer.appendChild(input);
    inputContainer.appendChild(btnWrapper);

    promptBar.appendChild(inputContainer);

    chatInterface.appendChild(header);
    chatInterface.appendChild(messagesArea);
    chatInterface.appendChild(promptBar);
    
    wrapper.appendChild(emptyState);
    wrapper.appendChild(chatInterface);
    container.appendChild(wrapper);

    // Fetch and populate models
    (async () => {
        try {
            if (client.getProviders && client.getModels) {
                const providersRaw = await client.getProviders();
                const providerList = Array.isArray(providersRaw) ? providersRaw : (providersRaw.providers || providersRaw.data || Object.values(providersRaw) || []);
                
                let foundAny = false;
                for (const p of providerList) {
                    if (!p || (!p.id && !p.name)) continue;
                    const modelsRaw = await client.getModels(p.id || p.name);
                    const modelList = Array.isArray(modelsRaw) ? modelsRaw : (modelsRaw.models || modelsRaw.data || Object.values(modelsRaw) || []);
                    
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
        const isBusy = status === 'busy';
        input.disabled = isBusy;
        sendBtn.disabled = isBusy;
        abortBtn.style.display = isBusy ? 'inline-block' : 'none';
        sendBtn.style.display = isBusy ? 'none' : 'inline-block';
        if (headerTitle) {
            headerTitle.textContent = `${activeSession?.title || 'Untitled'} - ${status}`;
        }
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
        input.disabled = true;
        
        try {
            let resp;
            if (client.promptSync) {
                resp = await client.promptSync(activeSession.id, text, mode, modelObj);
            } else {
                const dir = localStorage.getItem('oc_active_project');
                const headers = { 'Content-Type': 'application/json' };
                if (dir) headers['x-opencode-directory'] = dir;
                
                resp = await fetch(`/api/opencode/session/${activeSession.id}/message`, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ parts: [{ type: 'text', text: text }], agent: mode, model: modelObj })
                });
            }
            if (!resp.ok) {
                const errText = await resp.text().catch(() => '');
                throw new Error(`Server returned ${resp.status}: ${errText}`);
            }
        } catch (err) {
            console.error('Failed to send message:', err);
            input.disabled = false;
            alert('Failed to send message: ' + err.message);
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
            await fetch(`/api/opencode/session/${activeSession.id}/abort`, { method: 'POST' });
        } catch(e) {
            console.error('Abort failed', e);
        }
    });

    diffBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        try {
            const res = await fetch(`/api/opencode/session/${activeSession.id}/diff`);
            const diffData = await res.json();
            // Show diff modal
            if (renderDiff) {
                // assume renderDiff handles showing it, or we create a modal here
                const diffElem = renderDiff(diffData.diff || '');
                const modal = document.createElement('div');
                modal.className = 'modal oc-diff-modal';
                modal.innerHTML = `<div class="modal-content"><div class="modal-header"><h3>Diff</h3><button class="close">x</button></div><div class="modal-body"></div></div>`;
                modal.querySelector('.modal-body').appendChild(diffElem);
                document.body.appendChild(modal);
                modal.querySelector('.close').onclick = () => modal.remove();
                modal.style.display = 'block';
            }
        } catch(e) { console.error('Diff error', e); }
    });

    todoBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        try {
            const res = await fetch(`/api/opencode/session/${activeSession.id}/todo`);
            const data = await res.json();
            const modal = document.createElement('div');
            modal.className = 'modal oc-todo-panel';
            modal.innerHTML = `<div class="modal-content"><div class="modal-header"><h3>Todo</h3><button class="close">x</button></div><div class="modal-body"><ul>${data.todos?.map(t => `<li class="oc-todo-item">${t}</li>`).join('') || 'No todos'}</ul></div></div>`;
            document.body.appendChild(modal);
            modal.querySelector('.close').onclick = () => modal.remove();
            modal.style.display = 'block';
        } catch(e) { console.error('Todo error', e); }
    });

    revertBtn.addEventListener('click', async () => {
        if (!activeSession) return;
        if (confirm('Are you sure you want to revert uncommitted changes in this session?')) {
            try {
                await fetch(`/api/opencode/session/${activeSession.id}/revert`, { method: 'POST' });
                alert('Reverted successfully');
            } catch(e) { console.error('Revert error', e); }
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
            if (p.type === 'tool' && (p.state?.status === 'pending' || p.state?.status === 'running')) return false;
            return true;
        });

        visibleParts.forEach(part => {
            const pEl = createPartElement(part);
            if (part.id) partElements.set(part.id, pEl);
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
        setSessionState(session.status || 'idle');
        isUserScrolledUp = false;

        try {
            const res = await fetch(`/api/opencode/session/${session.id}/message`);
            const data = await res.json();
            // OpenCode v2 returns { items: [{info, parts}], more, cursor }
            // But might also return a flat array or { messages: [...] }
            let messages = [];
            if (data.items && Array.isArray(data.items)) {
                messages = data.items;
            } else if (Array.isArray(data)) {
                messages = data;
            } else if (data.messages) {
                messages = data.messages;
            }
            messages.forEach(renderMessage);
        } catch (err) {
            console.error('Failed to load messages:', err);
        }
    };


    return {
        loadSession,
        getActiveSession: () => activeSession,
        handleEvent(event) {
            if (!event || !event.type || !event.data) return;
            const data = event.data;
            
            // Only handle events for the active session
            if (data.sessionId && activeSession && data.sessionId !== activeSession.id) return;
            // Or if data is the session itself
            if (event.type.startsWith('session.') && data.id !== activeSession?.id) return;

            if (event.type === 'session.updated' || event.type === 'session.status') {
                setSessionState(data.status);
                activeSession = { ...activeSession, ...data };
            } else if (event.type === 'message.created') {
                renderMessage(data);
            } else if (event.type === 'message.part.created') {
                const msgEl = messageElements.get(data.messageId);
                if (msgEl) {
                    const contentWrap = msgEl.querySelector('.oc-message-content');
                    if (contentWrap) {
                        const pEl = createPartElement(data.part);
                        partElements.set(data.part.id, pEl);
                        contentWrap.appendChild(pEl);
                        scrollToBottom();
                    }
                }
            } else if (event.type === 'message.part.delta') {
                const pEl = partElements.get(data.partId);
                if (pEl) {
                    // Primitive delta update (re-parse full text for markdown - in real app would use delta stream logic)
                    // For reasoning:
                    if (pEl.querySelector('.oc-thinking-block .content')) {
                        const content = pEl.querySelector('.oc-thinking-block .content');
                        content.textContent += data.delta;
                    }
                    scrollToBottom();
                }
            } else if (event.type === 'message.part.updated') {
                const pEl = partElements.get(data.part.id);
                if (pEl) {
                    // Re-render the entire part (tool state may have changed from running -> completed)
                    const newEl = createPartElement(data.part);
                    if (data.part.id) partElements.set(data.part.id, newEl);
                    pEl.replaceWith(newEl);
                    scrollToBottom();
                }
            } else if (event.type === 'permission.created' && renderPermission) {
                const pEl = renderPermission(data, client);
                messagesArea.appendChild(pEl);
                scrollToBottom();
            } else if (event.type === 'question.created' && renderQuestion) {
                const qEl = renderQuestion(data, client);
                messagesArea.appendChild(qEl);
                scrollToBottom();
            }
        },
        destroy() {
            wrapper.remove();
        }
    };
}
