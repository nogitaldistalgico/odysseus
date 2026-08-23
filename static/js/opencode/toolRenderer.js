/**
 * Renders OpenCode tool calls and results.
 * @module toolRenderer
 */

import { renderCodeBlock, renderDiff } from './codeRenderer.js';

/**
 * Renders a tool call into a DOM element.
 * @param {Object} part - The tool call part.
 * @param {string} part.type - 'tool_call'
 * @param {string} part.toolName - The name of the tool.
 * @param {Object} part.args - The arguments passed to the tool.
 * @param {string} part.id - The unique identifier.
 * @returns {HTMLElement} The constructed tool call element.
 */
export function renderToolCall(part) {
    const el = document.createElement('div');
    el.className = 'oc-tool-block oc-tool-call';
    el.id = `tool-call-${part.id}`;

    const header = document.createElement('div');
    header.className = 'oc-tool-header';
    header.innerHTML = `
        <span class="oc-tool-icon">🔧</span>
        <span class="oc-tool-name">${escapeHtml(part.toolName)}</span>
        <span class="oc-tool-toggle">▼</span>
    `;
    
    const body = document.createElement('div');
    body.className = 'oc-tool-body';
    
    // Render args based on tool type
    if (part.toolName === 'bash') {
        const cmd = part.args.command || part.args.CommandLine || JSON.stringify(part.args);
        body.innerHTML = renderCodeBlock(cmd, 'bash');
    } else if (['file_edit', 'file_write', 'file_patch'].includes(part.toolName)) {
        const filePath = part.args.path || part.args.TargetFile || part.args.file || 'Unknown file';
        const content = part.args.content || part.args.ReplacementContent || part.args.CodeContent || part.args.patch || '';
        body.innerHTML = `
            <div class="oc-tool-filepath">${escapeHtml(filePath)}</div>
            ${renderCodeBlock(content, 'javascript')} 
        `;
    } else if (['file_read', 'grep', 'find'].includes(part.toolName)) {
        body.innerHTML = renderCodeBlock(JSON.stringify(part.args, null, 2), 'json');
    } else {
        body.innerHTML = renderCodeBlock(JSON.stringify(part.args, null, 2), 'json');
    }

    // Default collapsed
    body.style.display = 'none';
    
    header.addEventListener('click', () => {
        const isCollapsed = body.style.display === 'none';
        body.style.display = isCollapsed ? 'block' : 'none';
        header.querySelector('.oc-tool-toggle').textContent = isCollapsed ? '▲' : '▼';
    });

    el.appendChild(header);
    el.appendChild(body);
    return el;
}

/**
 * Renders a tool result into a DOM element.
 * @param {Object} part - The tool result part.
 * @param {string} part.type - 'tool_result'
 * @param {string} part.toolName - The name of the tool.
 * @param {string} part.output - The output of the tool.
 * @param {string} part.id - The unique identifier.
 * @param {boolean} [part.error=false] - Whether the tool result is an error.
 * @returns {HTMLElement} The constructed tool result element.
 */
export function renderToolResult(part) {
    const el = document.createElement('div');
    el.className = `oc-tool-block oc-tool-result ${part.error ? 'oc-tool-error' : ''}`;
    el.id = `tool-result-${part.id}`;

    const header = document.createElement('div');
    header.className = 'oc-tool-header';
    header.innerHTML = `
        <span class="oc-tool-icon">${part.error ? '❌' : '✅'}</span>
        <span class="oc-tool-name">${escapeHtml(part.toolName)} Result</span>
        <span class="oc-tool-toggle">${part.error ? '▲' : '▼'}</span>
    `;

    const body = document.createElement('div');
    body.className = 'oc-tool-body';
    
    if (part.toolName === 'bash') {
        body.innerHTML = renderCodeBlock(part.output, 'bash');
        body.classList.add('oc-terminal-output');
    } else if (['file_edit', 'file_write', 'file_patch'].includes(part.toolName)) {
        // Might include a diff in the output
        if (part.output.includes('---') && part.output.includes('+++')) {
            body.innerHTML = renderDiff(part.output);
        } else {
            body.innerHTML = renderCodeBlock(part.output, 'text');
        }
    } else {
        body.innerHTML = renderCodeBlock(part.output, 'text');
    }

    // Default: collapsed if success, expanded if error
    body.style.display = part.error ? 'block' : 'none';

    header.addEventListener('click', () => {
        const isCollapsed = body.style.display === 'none';
        body.style.display = isCollapsed ? 'block' : 'none';
        header.querySelector('.oc-tool-toggle').textContent = isCollapsed ? '▲' : '▼';
    });

    el.appendChild(header);
    el.appendChild(body);
    return el;
}

/**
 * Live updates an existing tool result element.
 * @param {HTMLElement} element - The tool result element to update.
 * @param {Object} part - The updated tool result part.
 */
export function updateToolResult(element, part) {
    if (!element || !part) return;
    const body = element.querySelector('.oc-tool-body');
    if (!body) return;
    
    if (part.toolName === 'bash') {
        body.innerHTML = renderCodeBlock(part.output, 'bash');
    } else if (['file_edit', 'file_write', 'file_patch'].includes(part.toolName)) {
        if (part.output.includes('---') && part.output.includes('+++')) {
            body.innerHTML = renderDiff(part.output);
        } else {
            body.innerHTML = renderCodeBlock(part.output, 'text');
        }
    } else {
        body.innerHTML = renderCodeBlock(part.output, 'text');
    }

    if (part.error) {
        element.classList.add('oc-tool-error');
        const icon = element.querySelector('.oc-tool-icon');
        if (icon) icon.textContent = '❌';
        body.style.display = 'block';
        const toggle = element.querySelector('.oc-tool-toggle');
        if (toggle) toggle.textContent = '▲';
    }
}

/**
 * Helper to escape HTML characters.
 * @param {string} str - The string to escape.
 * @returns {string} Escaped string.
 */
function escapeHtml(str) {
    if (typeof str !== 'string') return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
