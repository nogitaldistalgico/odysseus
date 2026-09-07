/**
 * Renders opencode permission requests and questions inline.
 *
 * Both payloads follow packages/schema/src/v1/{permission,question}.ts:
 *
 *   permission.asked -> { id, sessionID, permission, patterns, metadata, always, tool? }
 *   reply body       -> { reply: "once" | "always" | "reject", message? }
 *
 *   question.asked   -> { id, sessionID, questions: QuestionInfo[], tool? }
 *   reply body       -> { answers: string[][] }   // one array of labels per question
 *
 * @module permissionUI
 */

/**
 * Renders a permission request inline card.
 * @param {Object} permission - The `permission.asked` payload.
 * @param {Function} onReply - Receives (requestID, 'once'|'always'|'reject').
 * @returns {HTMLElement} The permission card element.
 */
export function renderPermission(permission, onReply) {
    const el = document.createElement('div');
    el.className = 'oc-permission-card';
    el.id = `permission-${permission.id}`;

    const title = document.createElement('h4');
    title.className = 'oc-card-title';
    const icon = document.createElement('span');
    icon.className = 'oc-card-icon';
    icon.textContent = '⚠';
    const titleText = document.createElement('span');
    // `permission` is the rule name opencode is asking about, e.g. "bash".
    titleText.textContent = `Permission required: ${permission.permission || 'unknown'}`;
    title.appendChild(icon);
    title.appendChild(titleText);

    const desc = document.createElement('p');
    desc.className = 'oc-permission-desc';
    const patterns = Array.isArray(permission.patterns) ? permission.patterns.filter(Boolean) : [];
    desc.textContent = patterns.length
        ? `Matches: ${patterns.join(', ')}`
        : 'This action requires your approval.';

    el.appendChild(title);
    el.appendChild(desc);

    // `metadata` carries the tool's arguments; show it only when there is one.
    if (permission.metadata && Object.keys(permission.metadata).length > 0) {
        const argsPre = document.createElement('pre');
        argsPre.className = 'oc-permission-args';
        argsPre.textContent = JSON.stringify(permission.metadata, null, 2);
        el.appendChild(argsPre);
    }

    const controls = document.createElement('div');
    controls.className = 'oc-permission-controls';

    const btnGroup = document.createElement('div');
    btnGroup.className = 'oc-permission-btns';

    const mkBtn = (label, reply, cls) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `oc-btn ${cls}`;
        b.textContent = label;
        b.addEventListener('click', () => {
            btnGroup.querySelectorAll('button').forEach(x => { x.disabled = true; });
            onReply(permission.id, reply);
        });
        return b;
    };

    btnGroup.appendChild(mkBtn('Reject', 'reject', 'oc-btn-reject'));
    // "always" is opencode's remember-my-choice: it writes a persisted rule.
    if (Array.isArray(permission.always) && permission.always.length > 0) {
        btnGroup.appendChild(mkBtn('Always allow', 'always', 'oc-btn-secondary'));
    }
    btnGroup.appendChild(mkBtn('Allow once', 'once', 'oc-btn-approve'));

    controls.appendChild(btnGroup);
    el.appendChild(controls);

    return el;
}

/**
 * Renders a question request card.
 *
 * One request can carry several questions; opencode expects the answers back
 * in the same order, each as an array of selected option labels.
 *
 * @param {Object} request - The `question.asked` payload.
 * @param {Function} onAnswer - Receives (requestID, string[][]).
 * @param {Function} [onReject] - Receives (requestID).
 * @returns {HTMLElement} The question card element.
 */
export function renderQuestion(request, onAnswer, onReject) {
    const el = document.createElement('div');
    el.className = 'oc-question-card';
    el.id = `question-${request.id}`;

    const title = document.createElement('h4');
    title.className = 'oc-card-title';
    const icon = document.createElement('span');
    icon.className = 'oc-card-icon';
    icon.textContent = '?';
    title.appendChild(icon);
    title.appendChild(document.createTextNode('Question'));
    el.appendChild(title);

    const questions = Array.isArray(request.questions) ? request.questions : [];
    /** @type {string[][]} */
    const answers = questions.map(() => []);
    /** @type {Array<() => void>} */
    const refreshers = [];

    const submit = () => {
        el.querySelectorAll('button, input').forEach(x => { x.disabled = true; });
        onAnswer(request.id, answers);
    };

    const syncSubmitState = () => {
        // Every question needs at least one answer before we can reply.
        submitBtn.disabled = answers.some(a => a.length === 0);
    };

    questions.forEach((q, qi) => {
        const block = document.createElement('div');
        block.className = 'oc-question-block';

        if (q.header) {
            const header = document.createElement('div');
            header.className = 'oc-question-header';
            header.textContent = q.header;
            block.appendChild(header);
        }

        const textEl = document.createElement('p');
        textEl.className = 'oc-question-text';
        textEl.textContent = q.question || '';
        block.appendChild(textEl);

        const inputArea = document.createElement('div');
        inputArea.className = 'oc-question-input-area';

        const options = Array.isArray(q.options) ? q.options : [];
        options.forEach(opt => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'oc-btn-option';
            const label = document.createElement('span');
            label.className = 'oc-option-label';
            label.textContent = opt.label;
            btn.appendChild(label);
            if (opt.description) {
                const d = document.createElement('span');
                d.className = 'oc-option-desc';
                d.textContent = opt.description;
                btn.appendChild(d);
            }
            btn.addEventListener('click', () => {
                if (q.multiple) {
                    const at = answers[qi].indexOf(opt.label);
                    if (at === -1) answers[qi].push(opt.label);
                    else answers[qi].splice(at, 1);
                } else {
                    answers[qi] = [opt.label];
                }
                refreshers[qi]();
                syncSubmitState();
                // Single-choice with one question is a one-tap interaction.
                if (!q.multiple && questions.length === 1 && q.custom === false) submit();
            });
            inputArea.appendChild(btn);
        });

        // `custom` defaults to true — a free-text answer is allowed unless
        // opencode explicitly turns it off.
        if (q.custom !== false) {
            const customWrap = document.createElement('div');
            customWrap.className = 'oc-question-custom';
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'oc-question-input';
            input.placeholder = options.length ? 'Or type your own answer…' : 'Type your answer…';
            const apply = () => {
                const v = input.value.trim();
                answers[qi] = v ? [v] : [];
                refreshers[qi]();
                syncSubmitState();
            };
            input.addEventListener('input', apply);
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !submitBtn.disabled) { e.preventDefault(); submit(); }
            });
            customWrap.appendChild(input);
            inputArea.appendChild(customWrap);
        }

        refreshers[qi] = () => {
            inputArea.querySelectorAll('.oc-btn-option').forEach((b, i) => {
                const chosen = options[i] && answers[qi].includes(options[i].label);
                b.classList.toggle('selected', !!chosen);
            });
        };

        block.appendChild(inputArea);
        el.appendChild(block);
    });

    const controls = document.createElement('div');
    controls.className = 'oc-permission-controls';
    const btnGroup = document.createElement('div');
    btnGroup.className = 'oc-permission-btns';

    if (onReject) {
        const rejectBtn = document.createElement('button');
        rejectBtn.type = 'button';
        rejectBtn.className = 'oc-btn oc-btn-reject';
        rejectBtn.textContent = 'Skip';
        rejectBtn.addEventListener('click', () => {
            el.querySelectorAll('button, input').forEach(x => { x.disabled = true; });
            onReject(request.id);
        });
        btnGroup.appendChild(rejectBtn);
    }

    const submitBtn = document.createElement('button');
    submitBtn.type = 'button';
    submitBtn.className = 'oc-btn oc-btn-approve';
    submitBtn.textContent = 'Send answer';
    submitBtn.disabled = true;
    submitBtn.addEventListener('click', submit);
    btnGroup.appendChild(submitBtn);

    controls.appendChild(btnGroup);
    el.appendChild(controls);

    return el;
}

/**
 * Removes a permission card from the DOM.
 * @param {string} requestId
 */
export function removePermission(requestId) {
    document.getElementById(`permission-${requestId}`)?.remove();
}

/**
 * Removes a question card from the DOM.
 * @param {string} requestId
 */
export function removeQuestion(requestId) {
    document.getElementById(`question-${requestId}`)?.remove();
}
