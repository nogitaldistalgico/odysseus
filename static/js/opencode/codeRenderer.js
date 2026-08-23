/**
 * Renders code blocks and diffs from OpenCode responses.
 * @module codeRenderer
 */

/**
 * Renders a code block with an optional language label and a copy button.
 * @param {string} code - The code content.
 * @param {string} [language=''] - The programming language for syntax highlighting styling.
 * @param {boolean} [showLineNumbers=false] - Whether to render line numbers.
 * @returns {string} The HTML string representing the code block.
 */
export function renderCodeBlock(code, language = '', showLineNumbers = false) {
    if (typeof code !== 'string') code = '';
    
    // Escape HTML to prevent XSS
    const escapedCode = escapeHtml(code);
    const langLabel = language ? `<div class="oc-code-lang">${escapeHtml(language)}</div>` : '';
    
    let content = escapedCode;
    if (showLineNumbers) {
        const lines = escapedCode.split('\n');
        content = lines.map((line, idx) => 
            `<div class="oc-line"><span class="oc-line-num">${idx + 1}</span><span class="oc-line-content">${line}</span></div>`
        ).join('\n');
    }

    return `
        <div class="oc-code-container">
            <div class="oc-code-header">
                ${langLabel}
                <button class="oc-copy-btn" onclick="navigator.clipboard.writeText(this.parentElement.nextElementSibling.innerText)">Copy</button>
            </div>
            <pre class="oc-code-block"><code class="language-${escapeHtml(language)}">${content}</code></pre>
        </div>
    `;
}

/**
 * Renders a unified diff as HTML.
 * @param {string} diffText - The raw unified diff string.
 * @returns {string} The HTML string representing the diff.
 */
export function renderDiff(diffText) {
    if (typeof diffText !== 'string') diffText = '';

    const lines = diffText.split('\n');
    let html = '<div class="oc-diff-block">';
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const escapedLine = escapeHtml(line);
        
        let lineClass = '';
        if (line.startsWith('+') && !line.startsWith('+++')) {
            lineClass = 'oc-diff-add';
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            lineClass = 'oc-diff-del';
        } else if (line.startsWith('@@')) {
            lineClass = 'oc-diff-hunk';
        }

        html += `<div class="oc-diff-line ${lineClass}"><span class="oc-line-num">${i + 1}</span><span class="oc-diff-content">${escapedLine}</span></div>`;
    }
    
    html += '</div>';
    return html;
}

/**
 * Renders inline code.
 * @param {string} text - The inline code string.
 * @returns {string} The HTML string for the inline code.
 */
export function renderInlineCode(text) {
    if (typeof text !== 'string') text = '';
    return `<code class="oc-inline-code">${escapeHtml(text)}</code>`;
}

/**
 * Helper to escape HTML characters.
 * @param {string} str - The string to escape.
 * @returns {string} Escaped string.
 */
function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
