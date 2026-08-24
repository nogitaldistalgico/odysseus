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
    el.id = `tool-call-${part.id || Date.now()}`;

    const toolName = part.name || part.toolName || 'tool';
    let argsObj = {};
    if (typeof part.arguments === 'string') {
        try { argsObj = JSON.parse(part.arguments); } catch(e) { argsObj = { raw: part.arguments }; }
    } else if (part.arguments) {
        argsObj = part.arguments;
    } else if (part.args) {
        argsObj = part.args;
    }

    const header = document.createElement('div');
    header.className = 'oc-tool-header';
    
    // Create a compact preview of the command/args
    let previewText = '';
    if (['bash', 'run_command', 'shell'].includes(toolName)) {
        previewText = argsObj.command || argsObj.CommandLine || argsObj.raw || '';
    } else if (['file_edit', 'file_write', 'file_patch', 'replace_file_content', 'write_to_file'].includes(toolName)) {
        previewText = argsObj.path || argsObj.TargetFile || argsObj.file || 'File';
    } else if (toolName.includes('read_') || toolName.includes('view_')) {
        previewText = argsObj.path || argsObj.AbsolutePath || argsObj.file || 'File';
    }
    
    // Truncate preview
    const maxLen = 60;
    if (previewText.length > maxLen) previewText = previewText.substring(0, maxLen) + '...';

    header.innerHTML = `
        <span class="oc-tool-name">${escapeHtml(toolName)}</span>
        <span class="oc-tool-preview" style="color:var(--fg); opacity:0.6; font-size:0.9em; margin-left:8px;">${escapeHtml(previewText)}</span>
        <span class="oc-tool-toggle" style="margin-left:auto;">▼</span>
    `;
    
    const body = document.createElement('div');
    body.className = 'oc-tool-body';
    
    // Render args based on tool type
    if (['bash', 'run_command', 'shell'].includes(toolName)) {
        const cmd = argsObj.command || argsObj.CommandLine || argsObj.raw || JSON.stringify(argsObj);
        body.innerHTML = renderCodeBlock(cmd, 'bash');
    } else if (['file_edit', 'file_write', 'file_patch', 'replace_file_content', 'write_to_file'].includes(toolName)) {
        const filePath = argsObj.path || argsObj.TargetFile || argsObj.file || 'Unknown file';
        const content = argsObj.content || argsObj.ReplacementContent || argsObj.CodeContent || argsObj.patch || '';
        body.innerHTML = `
            <div class="oc-tool-filepath" style="margin-bottom:8px; font-weight:bold;">${escapeHtml(filePath)}</div>
            ${renderCodeBlock(content, 'javascript')} 
        `;
    } else {
        body.innerHTML = renderCodeBlock(JSON.stringify(argsObj, null, 2), 'json');
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
    const isError = part.error || part.isError || false;
    el.className = `oc-tool-block oc-tool-result ${isError ? 'oc-tool-error' : ''}`;
    el.id = `tool-result-${part.id || Date.now()}`;

    const toolName = part.name || part.toolName || 'tool';
    let outputStr = '';
    if (typeof part.output === 'string') {
        outputStr = part.output;
    } else if (typeof part.content === 'string') {
        outputStr = part.content;
    } else if (part.output || part.content || part.result) {
        outputStr = JSON.stringify(part.output || part.content || part.result, null, 2);
    }

    const header = document.createElement('div');
    header.className = 'oc-tool-header';
    header.innerHTML = `
        <span class="oc-tool-name">${escapeHtml(toolName)} Result</span>
        <span class="oc-tool-toggle" style="margin-left:auto;">${isError ? '▲' : '▼'}</span>
    `;

    const body = document.createElement('div');
    body.className = 'oc-tool-body';
    
    if (['bash', 'run_command', 'shell'].includes(toolName)) {
        body.innerHTML = renderCodeBlock(outputStr, 'bash');
        body.classList.add('oc-terminal-output');
    } else if (['file_edit', 'file_write', 'file_patch', 'replace_file_content'].includes(toolName)) {
        // Might include a diff in the output
        if (outputStr.includes('---') && outputStr.includes('+++')) {
            body.innerHTML = renderDiff(outputStr);
        } else {
            body.innerHTML = renderCodeBlock(outputStr, 'text');
        }
    } else {
        body.innerHTML = renderCodeBlock(outputStr, 'text');
    }

    // Default: collapsed if success, expanded if error
    body.style.display = isError ? 'block' : 'none';

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
    
    const toolName = part.name || part.toolName || 'tool';
    let outputStr = '';
    if (typeof part.output === 'string') {
        outputStr = part.output;
    } else if (typeof part.content === 'string') {
        outputStr = part.content;
    } else if (part.output || part.content || part.result) {
        outputStr = JSON.stringify(part.output || part.content || part.result, null, 2);
    }

    if (['bash', 'run_command', 'shell'].includes(toolName)) {
        body.innerHTML = renderCodeBlock(outputStr, 'bash');
    } else if (['file_edit', 'file_write', 'file_patch', 'replace_file_content'].includes(toolName)) {
        if (outputStr.includes('---') && outputStr.includes('+++')) {
            body.innerHTML = renderDiff(outputStr);
        } else {
            body.innerHTML = renderCodeBlock(outputStr, 'text');
        }
    } else {
        body.innerHTML = renderCodeBlock(outputStr, 'text');
    }

    if (part.error || part.isError) {
        element.classList.add('oc-tool-error');
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
