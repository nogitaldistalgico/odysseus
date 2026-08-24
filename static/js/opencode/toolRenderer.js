/**
 * Renders OpenCode tool calls and results.
 * 
 * OpenCode message parts with type === "tool" contain:
 *   - part.tool: string (e.g. "bash", "read", "write", "edit", "grep", "glob", "list", "webfetch", "task", "todowrite")
 *   - part.state.status: "pending" | "running" | "completed" | "error"
 *   - part.state.input: object (tool-specific inputs)
 *   - part.state.output: string (tool output, only when completed)
 *   - part.state.metadata: object (tool-specific metadata like diffs, match counts)
 *   - part.state.time: { start, end } (timestamps)
 *
 * @module toolRenderer
 */

// ──────────────────────────────────────────────────────────────────────
//  Helpers
// ──────────────────────────────────────────────────────────────────────

function escapeHtml(str) {
    if (typeof str !== 'string') return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/** Strip the working directory prefix from a file path for cleaner display. */
function stripCwd(filePath, cwd) {
    if (!filePath || !cwd) return filePath || '';
    const prefix = cwd.endsWith('/') ? cwd : cwd + '/';
    if (filePath === cwd) return '';
    if (filePath.startsWith(prefix)) return filePath.slice(prefix.length);
    return filePath;
}

/** Infer a syntax-highlighting language from a filename extension. */
function langFromFile(filename) {
    if (!filename) return 'text';
    const ext = filename.split('.').pop()?.toLowerCase() ?? '';
    const map = {
        py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript',
        tsx: 'typescript', html: 'html', css: 'css', json: 'json', md: 'markdown',
        sh: 'bash', bash: 'bash', zsh: 'bash', yaml: 'yaml', yml: 'yaml',
        toml: 'toml', rs: 'rust', go: 'go', rb: 'ruby', java: 'java',
        c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp', swift: 'swift',
        kt: 'kotlin', sql: 'sql', xml: 'xml', svg: 'xml',
    };
    return map[ext] || 'text';
}

/** Simple code block rendering (no shiki, just monospace with proper escaping). */
function codeBlock(code, lang) {
    if (!code) return '';
    return `<pre class="oc-code-block" data-lang="${escapeHtml(lang || 'text')}"><code>${escapeHtml(code)}</code></pre>`;
}

/** Render a unified diff with red/green coloring. */
function renderDiff(diffText) {
    if (!diffText) return '';
    const lines = diffText.split('\n').map(line => {
        if (line.startsWith('+') && !line.startsWith('+++')) {
            return `<span class="oc-diff-add">${escapeHtml(line)}</span>`;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            return `<span class="oc-diff-del">${escapeHtml(line)}</span>`;
        } else if (line.startsWith('@@')) {
            return `<span class="oc-diff-hunk">${escapeHtml(line)}</span>`;
        }
        return escapeHtml(line);
    });
    return `<pre class="oc-code-block oc-diff-block"><code>${lines.join('\n')}</code></pre>`;
}

// ──────────────────────────────────────────────────────────────────────
//  Icon map
// ──────────────────────────────────────────────────────────────────────
const TOOL_ICONS = {
    bash: '⌨️', shell: '⌨️', run_command: '⌨️',
    read: '📄', read_file: '📄', view_file: '📄',
    write: '📝', write_to_file: '📝', file_write: '📝',
    edit: '✏️', file_edit: '✏️', replace_file_content: '✏️', str_replace_editor: '✏️',
    grep: '🔍', file_search: '🔍', find_by_name: '🔍',
    glob: '🔎', list: '📁',
    webfetch: '🌐', web_fetch: '🌐',
    task: '🤖', todowrite: '📋',
};

function toolIcon(toolName) {
    return TOOL_ICONS[toolName] || '🔧';
}

// ──────────────────────────────────────────────────────────────────────
//  Tool title labels (matching OpenCode's UI exactly)
// ──────────────────────────────────────────────────────────────────────
const TOOL_LABELS = {
    bash: 'Shell', shell: 'Shell', run_command: 'Shell',
    read: 'Lesen', read_file: 'Lesen', view_file: 'Lesen',
    write: 'Schreiben', write_to_file: 'Schreiben', file_write: 'Schreiben',
    edit: 'Bearbeiten', file_edit: 'Bearbeiten', replace_file_content: 'Bearbeiten', str_replace_editor: 'Bearbeiten',
    grep: 'Grep', file_search: 'Grep', find_by_name: 'Glob',
    glob: 'Glob', list: 'LS',
    webfetch: 'Fetch', web_fetch: 'Fetch',
    task: 'Task', todowrite: 'Plan',
};

function toolLabel(toolName) {
    return TOOL_LABELS[toolName] || toolName;
}

// ──────────────────────────────────────────────────────────────────────
//  Extract a compact target/preview from tool input
// ──────────────────────────────────────────────────────────────────────
function toolTarget(toolName, input, cwd) {
    if (!input) return '';
    switch (toolName) {
        case 'bash': case 'shell': case 'run_command':
            return input.command || input.CommandLine || '';
        case 'read': case 'read_file': case 'view_file':
            return stripCwd(input.filePath || input.path || input.AbsolutePath || '', cwd);
        case 'write': case 'write_to_file': case 'file_write':
            return stripCwd(input.filePath || input.path || input.TargetFile || '', cwd);
        case 'edit': case 'file_edit': case 'replace_file_content': case 'str_replace_editor':
            return stripCwd(input.filePath || input.path || input.TargetFile || '', cwd);
        case 'grep': case 'file_search':
            return `"${input.pattern || input.query || input.Query || ''}"`;
        case 'glob': case 'find_by_name':
            return `"${input.pattern || input.Pattern || ''}"`;
        case 'list':
            return stripCwd(input.path || '', cwd);
        case 'webfetch': case 'web_fetch':
            return input.url || input.Url || '';
        case 'task':
            return input.description || '';
        case 'todowrite':
            return '';
        default:
            return '';
    }
}

// ──────────────────────────────────────────────────────────────────────
//  Collapsible details button (matches OpenCode's ResultsButton)
// ──────────────────────────────────────────────────────────────────────
function createResultsButton(labelShow, labelHide, contentHtml) {
    const wrapper = document.createElement('div');
    wrapper.className = 'oc-results-wrapper';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'oc-results-btn';
    btn.innerHTML = `<span class="oc-results-label">${escapeHtml(labelShow)}</span> <span class="oc-results-chevron">▶</span>`;

    const content = document.createElement('div');
    content.className = 'oc-results-content';
    content.style.display = 'none';
    content.innerHTML = contentHtml;

    let expanded = false;
    btn.addEventListener('click', () => {
        expanded = !expanded;
        content.style.display = expanded ? 'block' : 'none';
        btn.querySelector('.oc-results-chevron').textContent = expanded ? '▼' : '▶';
        btn.querySelector('.oc-results-label').textContent = expanded ? labelHide : labelShow;
    });

    wrapper.appendChild(btn);
    wrapper.appendChild(content);
    return wrapper;
}

// ──────────────────────────────────────────────────────────────────────
//  Main: Render a tool part (type === "tool")
// ──────────────────────────────────────────────────────────────────────

/**
 * Renders an OpenCode tool part.
 * @param {Object} part - A part with type === "tool"
 * @param {string} [cwd] - The working directory for path stripping
 * @returns {HTMLElement}
 */
export function renderToolPart(part, cwd) {
    const el = document.createElement('div');
    el.className = 'oc-tool-block';
    el.dataset.tool = part.tool || '';
    el.dataset.status = part.state?.status || 'unknown';
    if (part.id) el.id = `tool-${part.id}`;

    const tool = part.tool || 'unknown';
    const state = part.state || {};
    const input = state.input || {};
    const status = state.status || 'pending';

    // ── Title row ──
    const titleRow = document.createElement('div');
    titleRow.className = 'oc-tool-title';
    
    const icon = document.createElement('span');
    icon.className = 'oc-tool-icon';
    icon.textContent = toolIcon(tool);
    
    const label = document.createElement('span');
    label.className = 'oc-tool-label';
    label.textContent = toolLabel(tool);

    const target = document.createElement('span');
    target.className = 'oc-tool-target';
    const targetText = toolTarget(tool, input, cwd);
    target.textContent = targetText;
    if (input.filePath || input.path) target.title = input.filePath || input.path;

    titleRow.appendChild(icon);
    titleRow.appendChild(label);
    if (targetText) titleRow.appendChild(target);

    el.appendChild(titleRow);

    // ── Status indicator for pending/running ──
    if (status === 'pending' || status === 'running') {
        const spinner = document.createElement('div');
        spinner.className = 'oc-tool-spinner';
        spinner.innerHTML = '<span class="oc-spinner-dot">⏳</span> <span class="oc-spinner-text">Running...</span>';
        el.appendChild(spinner);
        return el;
    }

    // ── Error state ──
    if (status === 'error') {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'oc-tool-error-content';
        errorDiv.innerHTML = `<pre class="oc-error-text">${escapeHtml(state.error || 'Unknown error')}</pre>`;
        el.appendChild(errorDiv);
        return el;
    }

    // ── Completed: render tool-specific content ──
    const metadata = state.metadata || {};
    const output = state.output || '';

    switch (tool) {
        case 'bash': case 'shell': case 'run_command': {
            // Bash: show command block + collapsible output
            const cmdBlock = document.createElement('div');
            cmdBlock.className = 'oc-bash-block';
            cmdBlock.innerHTML = codeBlock(`$ ${input.command || ''}`, 'bash');
            el.appendChild(cmdBlock);

            const stdout = metadata.output || metadata.stdout || output;
            if (stdout) {
                const outputBlock = document.createElement('div');
                outputBlock.className = 'oc-bash-output';
                outputBlock.innerHTML = codeBlock(stdout, 'console');
                el.appendChild(outputBlock);
            }
            break;
        }

        case 'read': case 'read_file': case 'view_file': {
            // Read: show preview if available
            if (typeof metadata.preview === 'string') {
                const filePath = input.filePath || input.path || '';
                el.appendChild(createResultsButton(
                    'Vorschau anzeigen', 'Vorschau ausblenden',
                    codeBlock(metadata.preview, langFromFile(filePath))
                ));
            } else if (output) {
                el.appendChild(createResultsButton(
                    'Ergebnis anzeigen', 'Ergebnis ausblenden',
                    codeBlock(output, 'text')
                ));
            }
            break;
        }

        case 'write': case 'write_to_file': case 'file_write': {
            // Write: show written content
            if (input.content) {
                const filePath = input.filePath || input.path || '';
                el.appendChild(createResultsButton(
                    'Inhalt anzeigen', 'Inhalt ausblenden',
                    codeBlock(input.content, langFromFile(filePath))
                ));
            }
            break;
        }

        case 'edit': case 'file_edit': case 'replace_file_content': case 'str_replace_editor': {
            // Edit: show diff if available
            if (metadata.diff) {
                const diffDiv = document.createElement('div');
                diffDiv.className = 'oc-diff-container';
                diffDiv.innerHTML = renderDiff(metadata.diff);
                el.appendChild(diffDiv);
            } else if (output) {
                el.appendChild(createResultsButton(
                    'Ergebnis anzeigen', 'Ergebnis ausblenden',
                    codeBlock(output, 'text')
                ));
            }
            break;
        }

        case 'grep': case 'file_search': {
            const count = metadata.matches || 0;
            if (count > 0 && output) {
                el.appendChild(createResultsButton(
                    `${count} Treffer`, 'Ausblenden',
                    codeBlock(output, 'text')
                ));
            } else if (output) {
                const resultDiv = document.createElement('div');
                resultDiv.className = 'oc-tool-dimmed';
                resultDiv.textContent = output;
                el.appendChild(resultDiv);
            }
            break;
        }

        case 'glob': case 'find_by_name': {
            const count = metadata.count || 0;
            if (count > 0 && output) {
                el.appendChild(createResultsButton(
                    `${count} Ergebnis(se)`, 'Ausblenden',
                    codeBlock(output, 'text')
                ));
            } else if (output) {
                el.appendChild(createResultsButton(
                    'Ergebnis anzeigen', 'Ergebnis ausblenden',
                    codeBlock(output, 'text')
                ));
            }
            break;
        }

        case 'list': {
            if (output) {
                el.appendChild(createResultsButton(
                    'Ergebnis anzeigen', 'Ergebnis ausblenden',
                    codeBlock(output, 'text')
                ));
            }
            break;
        }

        case 'webfetch': case 'web_fetch': {
            if (metadata.error) {
                const errorDiv = document.createElement('div');
                errorDiv.className = 'oc-tool-error-content';
                errorDiv.innerHTML = `<pre class="oc-error-text">${escapeHtml(output)}</pre>`;
                el.appendChild(errorDiv);
            } else if (output) {
                el.appendChild(createResultsButton(
                    'Ergebnis anzeigen', 'Ergebnis ausblenden',
                    codeBlock(output, input.format || 'text')
                ));
            }
            break;
        }

        case 'task': {
            if (input.prompt) {
                const promptDiv = document.createElement('div');
                promptDiv.className = 'oc-tool-prompt';
                promptDiv.textContent = `"${input.prompt}"`;
                el.appendChild(promptDiv);
            }
            if (output) {
                el.appendChild(createResultsButton(
                    'Ausgabe anzeigen', 'Ausgabe ausblenden',
                    `<div class="oc-task-output">${escapeHtml(output)}</div>`
                ));
            }
            break;
        }

        case 'todowrite': {
            const todos = input.todos || [];
            if (todos.length > 0) {
                const ul = document.createElement('ul');
                ul.className = 'oc-todo-list';
                for (const todo of todos) {
                    const li = document.createElement('li');
                    li.className = 'oc-todo-item';
                    li.dataset.status = todo.status || 'pending';
                    const check = todo.status === 'completed' ? '✅'
                        : todo.status === 'in_progress' ? '🔄' : '⬜';
                    li.innerHTML = `<span class="oc-todo-check">${check}</span> ${escapeHtml(todo.content || '')}`;
                    ul.appendChild(li);
                }
                el.appendChild(ul);
            }
            break;
        }

        default: {
            // Fallback: show input args as a table + output
            if (input && Object.keys(input).length > 0) {
                const argsDiv = document.createElement('div');
                argsDiv.className = 'oc-tool-args';
                for (const [key, val] of Object.entries(input)) {
                    const row = document.createElement('div');
                    row.className = 'oc-tool-arg-row';
                    const valStr = typeof val === 'string' || typeof val === 'number' || typeof val === 'boolean'
                        ? String(val) : val == null ? '' : JSON.stringify(val);
                    row.innerHTML = `<span class="oc-arg-key">${escapeHtml(key)}</span><span class="oc-arg-val">${escapeHtml(valStr)}</span>`;
                    argsDiv.appendChild(row);
                }
                el.appendChild(argsDiv);
            }
            if (output) {
                el.appendChild(createResultsButton(
                    'Ergebnis anzeigen', 'Ergebnis ausblenden',
                    codeBlock(output, 'text')
                ));
            }
            break;
        }
    }

    return el;
}

// ──────────────────────────────────────────────────────────────────────
//  Legacy wrappers (keep backward compatibility with chatView.js)
// ──────────────────────────────────────────────────────────────────────

/** @deprecated Use renderToolPart instead */
export function renderToolCall(part) {
    return renderToolPart(part);
}

/** @deprecated Use renderToolPart instead */
export function renderToolResult(part) {
    return renderToolPart(part);
}

/** @deprecated Use renderToolPart instead */
export function updateToolResult(element, part) {
    if (!element || !part) return;
    const newEl = renderToolPart(part);
    element.replaceWith(newEl);
}
