/**
 * Renders permission request and question dialogs inline for OpenCode.
 * @module permissionUI
 */

/**
 * Renders a permission request inline card.
 * @param {Object} permission - The permission event data.
 * @param {string} permission.permissionID - Unique ID for the permission.
 * @param {string} permission.toolName - The tool requesting permission.
 * @param {Object} permission.args - Tool arguments.
 * @param {string} permission.description - Description of what the tool wants to do.
 * @param {Function} onApprove - Callback when approved. Receives (permissionID, remember).
 * @param {Function} onReject - Callback when rejected. Receives (permissionID).
 * @returns {HTMLElement} The permission card element.
 */
export function renderPermission(permission, onApprove, onReject) {
    const el = document.createElement('div');
    el.className = 'oc-permission-card';
    el.id = `permission-${permission.permissionID}`;

    const title = document.createElement('h4');
    title.innerHTML = `⚠️ Permission Required: <span>${escapeHtml(permission.toolName)}</span>`;
    
    const desc = document.createElement('p');
    desc.className = 'oc-permission-desc';
    desc.textContent = permission.description || 'This action requires your approval.';

    const argsPre = document.createElement('pre');
    argsPre.className = 'oc-permission-args';
    argsPre.textContent = JSON.stringify(permission.args, null, 2);

    const controls = document.createElement('div');
    controls.className = 'oc-permission-controls';

    const rememberLabel = document.createElement('label');
    rememberLabel.className = 'oc-permission-remember';
    const rememberCheckbox = document.createElement('input');
    rememberCheckbox.type = 'checkbox';
    rememberLabel.appendChild(rememberCheckbox);
    rememberLabel.appendChild(document.createTextNode(' Remember my choice'));

    const btnGroup = document.createElement('div');
    btnGroup.className = 'oc-permission-btns';

    const approveBtn = document.createElement('button');
    approveBtn.className = 'oc-btn-approve';
    approveBtn.textContent = 'Approve';
    // Match Odysseus primary styling via CSS custom prop logic (assumed handled by CSS class or inline)
    approveBtn.style.backgroundColor = 'var(--accent, #007bff)';
    approveBtn.style.color = '#fff';
    approveBtn.onclick = () => onApprove(permission.permissionID, rememberCheckbox.checked);

    const rejectBtn = document.createElement('button');
    rejectBtn.className = 'oc-btn-reject';
    rejectBtn.textContent = 'Reject';
    rejectBtn.onclick = () => onReject(permission.permissionID);

    btnGroup.appendChild(rejectBtn);
    btnGroup.appendChild(approveBtn);

    controls.appendChild(rememberLabel);
    controls.appendChild(btnGroup);

    el.appendChild(title);
    el.appendChild(desc);
    el.appendChild(argsPre);
    el.appendChild(controls);

    return el;
}

/**
 * Renders a question card inline.
 * @param {Object} question - The question event data.
 * @param {string} question.questionID - Unique ID for the question.
 * @param {string} question.text - The question text.
 * @param {Array<string>} [question.options] - Optional multiple choice options.
 * @param {Function} onAnswer - Callback when answered. Receives (questionID, answerText).
 * @returns {HTMLElement} The question card element.
 */
export function renderQuestion(question, onAnswer) {
    const el = document.createElement('div');
    el.className = 'oc-question-card';
    el.id = `question-${question.questionID}`;

    const title = document.createElement('h4');
    title.textContent = '❓ Question';

    const textEl = document.createElement('p');
    textEl.className = 'oc-question-text';
    textEl.textContent = question.text;

    const inputArea = document.createElement('div');
    inputArea.className = 'oc-question-input-area';

    if (question.options && Array.isArray(question.options) && question.options.length > 0) {
        question.options.forEach(opt => {
            const btn = document.createElement('button');
            btn.className = 'oc-btn-option';
            btn.textContent = opt;
            btn.onclick = () => onAnswer(question.questionID, opt);
            inputArea.appendChild(btn);
        });
    } else {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'oc-question-input';
        input.placeholder = 'Type your answer...';
        
        const submitBtn = document.createElement('button');
        submitBtn.className = 'oc-btn-submit';
        submitBtn.textContent = 'Submit';
        submitBtn.style.backgroundColor = 'var(--accent, #007bff)';
        submitBtn.style.color = '#fff';
        
        const submit = () => {
            if (input.value.trim()) {
                onAnswer(question.questionID, input.value.trim());
            }
        };

        submitBtn.onclick = submit;
        input.onkeypress = (e) => {
            if (e.key === 'Enter') submit();
        };

        inputArea.appendChild(input);
        inputArea.appendChild(submitBtn);
    }

    el.appendChild(title);
    el.appendChild(textEl);
    el.appendChild(inputArea);

    return el;
}

/**
 * Removes a permission card from the DOM.
 * @param {string} permissionId - The ID of the permission.
 */
export function removePermission(permissionId) {
    const el = document.getElementById(`permission-${permissionId}`);
    if (el && el.parentNode) {
        el.parentNode.removeChild(el);
    }
}

/**
 * Removes a question card from the DOM.
 * @param {string} questionId - The ID of the question.
 */
export function removeQuestion(questionId) {
    const el = document.getElementById(`question-${questionId}`);
    if (el && el.parentNode) {
        el.parentNode.removeChild(el);
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
