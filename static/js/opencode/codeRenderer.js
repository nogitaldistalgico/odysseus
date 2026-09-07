/**
 * Renders code blocks and diffs from OpenCode responses.
 *
 * These builders return DOM **elements**, not HTML strings: the callers append
 * them with `appendChild`, which throws a TypeError on a string and used to
 * take the whole markdown block down with it. Building nodes also lets the copy
 * button use a real listener — the previous inline `onclick` was silently
 * blocked by the app's CSP (`script-src 'self' 'nonce-…'`, no 'unsafe-inline').
 *
 * @module codeRenderer
 */

/**
 * Renders a code block with an optional language label and a copy button.
 * @param {string} code - The code content.
 * @param {string} [language=''] - The language, used as a label and class.
 * @param {boolean} [showLineNumbers=false] - Whether to render line numbers.
 * @returns {HTMLElement} The code block element.
 */
export function renderCodeBlock(code, language = '', showLineNumbers = false) {
    if (typeof code !== 'string') code = '';

    const container = document.createElement('div');
    container.className = 'oc-code-container';

    const header = document.createElement('div');
    header.className = 'oc-code-header';

    const lang = document.createElement('div');
    lang.className = 'oc-code-lang';
    lang.textContent = language || '';
    header.appendChild(lang);

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'oc-copy-btn';
    copyBtn.textContent = 'Copy';
    copyBtn.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(code);
            copyBtn.textContent = 'Copied';
        } catch {
            copyBtn.textContent = 'Failed';
        }
        setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    });
    header.appendChild(copyBtn);

    const pre = document.createElement('pre');
    pre.className = 'oc-code-block';
    const codeEl = document.createElement('code');
    if (language) codeEl.className = `language-${language}`;

    if (showLineNumbers) {
        code.split('\n').forEach((line, idx) => {
            const row = document.createElement('div');
            row.className = 'oc-line';
            const num = document.createElement('span');
            num.className = 'oc-line-num';
            num.textContent = String(idx + 1);
            const content = document.createElement('span');
            content.className = 'oc-line-content';
            content.textContent = line;
            row.appendChild(num);
            row.appendChild(content);
            codeEl.appendChild(row);
        });
    } else {
        // textContent, so nothing in the model's output can become markup.
        codeEl.textContent = code;
    }

    pre.appendChild(codeEl);
    container.appendChild(header);
    container.appendChild(pre);
    return container;
}

/**
 * Renders a unified diff.
 * @param {string} diffText - The raw unified diff string.
 * @returns {HTMLElement} The diff element.
 */
export function renderDiff(diffText) {
    if (typeof diffText !== 'string') diffText = '';

    const block = document.createElement('div');
    block.className = 'oc-diff-block';

    diffText.split('\n').forEach((line, i) => {
        const row = document.createElement('div');
        row.className = 'oc-diff-line';
        if (line.startsWith('+') && !line.startsWith('+++')) row.classList.add('oc-diff-add');
        else if (line.startsWith('-') && !line.startsWith('---')) row.classList.add('oc-diff-del');
        else if (line.startsWith('@@')) row.classList.add('oc-diff-hunk');

        const num = document.createElement('span');
        num.className = 'oc-line-num';
        num.textContent = String(i + 1);

        const content = document.createElement('span');
        content.className = 'oc-diff-content';
        content.textContent = line;

        row.appendChild(num);
        row.appendChild(content);
        block.appendChild(row);
    });

    return block;
}

/**
 * Renders inline code.
 * @param {string} text - The inline code string.
 * @returns {HTMLElement} The inline code element.
 */
export function renderInlineCode(text) {
    const el = document.createElement('code');
    el.className = 'oc-inline-code';
    el.textContent = typeof text === 'string' ? text : '';
    return el;
}
