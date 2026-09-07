/**
 * Fitness Coach dashboard.
 *
 * The panel used to show three bare numbers and nothing else, even though a
 * per-day history was being written the whole time — for a coach the curve is
 * the information, not today's value. It now renders the deterministic
 * indicator bundle from /analysis, sparklines from /history, and the active
 * notes, and it states how confident a score is instead of presenting every
 * number as equally solid.
 */

const NUM = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 });

export function initFitnessModule(appElements, uiModule, sessionModule, chatModule) {
  const { el, API_BASE } = appElements;

  const fitnessBtn = el('tool-fitness-btn');
  const fitnessModal = el('fitness-modal');
  if (!fitnessBtn || !fitnessModal) return;

  const errorEl = el('fitness-error');

  function showError(msg) {
    if (!errorEl) return;
    errorEl.textContent = msg;
    errorEl.classList.add('visible');
    clearTimeout(showError._t);
    showError._t = setTimeout(() => errorEl.classList.remove('visible'), 8000);
  }
  function clearError() {
    errorEl?.classList.remove('visible');
  }

  async function api(path, opts) {
    const res = await fetch(`${API_BASE}${path}`, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || body.error || detail;
      } catch { /* non-JSON error body */ }
      throw new Error(detail);
    }
    return res.json();
  }

  function dismiss() { fitnessModal.classList.add('hidden'); }
  el('close-fitness-modal')?.addEventListener('click', dismiss);
  fitnessModal.addEventListener('click', (e) => { if (e.target === fitnessModal) dismiss(); });

  // ---------------------------------------------------------------
  //  Cards
  // ---------------------------------------------------------------

  function renderMetrics(data) {
    if (!data) return;
    const set = (id, value) => { const node = el(id); if (node) node.textContent = value; };

    if (data.recovery) {
      set('fitness-recovery-score', data.recovery.score ?? '--');
      set('fitness-recovery-text', data.recovery.text ?? '');
    }
    if (data.condition) {
      const c = data.condition;
      set('fitness-condition-score', c.score ?? '--');
      set('fitness-condition-text', c.tooltip || c.text || '');

      const trend = el('fitness-condition-trend');
      if (trend) {
        const dir = c.trend || 'neutral';
        trend.dataset.dir = dir;
        trend.textContent = dir === 'up' ? `▲ ${c.delta ?? ''}`
          : dir === 'down' ? `▼ ${Math.abs(c.delta ?? 0)}` : '';
      }
      // Confidence is surfaced rather than implied — a score computed from a
      // handful of days should not read as authoritative.
      const conf = el('fitness-condition-confidence');
      if (conf) {
        if (c.confidence) {
          conf.textContent = c.confidence;
          conf.dataset.level = c.confidence;
          conf.title = c.based_on_days != null
            ? `Basiert auf ${c.based_on_days} Tagen mit Daten`
            : '';
        } else {
          conf.textContent = '';
          conf.removeAttribute('data-level');
        }
      }
      const reco = el('fitness-recommendation');
      if (reco) {
        reco.textContent = c.recommendation || '';
        reco.hidden = !c.recommendation;
      }
    }
    if (data.movement) {
      const m = data.movement;
      set('fitness-movement-score', `${m.current ?? 0} / ${m.goal ?? 0} ${m.unit || 'kcal'}`);
      set('fitness-movement-text', m.text ?? '');
    }
  }

  // ---------------------------------------------------------------
  //  Notable findings
  // ---------------------------------------------------------------

  function renderNotable(list) {
    const host = el('fitness-notable');
    if (!host) return;
    host.innerHTML = '';
    if (!list || !list.length) {
      const li = document.createElement('li');
      li.className = 'fit-muted';
      li.textContent = 'Nichts Auffälliges in den letzten 14 Tagen.';
      host.appendChild(li);
      return;
    }
    // Severity travels with the finding; classifying it by matching substrings
    // of a human sentence breaks as soon as the wording changes.
    const order = { bad: 0, warn: 1, good: 2, neutral: 3 };
    const items = list
      .map(x => (typeof x === 'string' ? { text: x, severity: 'neutral' } : x))
      .sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));

    for (const item of items) {
      const li = document.createElement('li');
      li.dataset.sev = item.severity || 'neutral';
      li.textContent = item.text;
      host.appendChild(li);
    }
  }

  // ---------------------------------------------------------------
  //  Sparklines
  // ---------------------------------------------------------------

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function sparkline(points) {
    const W = 240, H = 40, PAD = 3;
    const values = points.map(p => p.value);
    const min = Math.min(...values), max = Math.max(...values);
    const span = (max - min) || 1;
    const stepX = points.length > 1 ? (W - PAD * 2) / (points.length - 1) : 0;
    const xy = points.map((p, i) => [
      PAD + i * stepX,
      PAD + (H - PAD * 2) * (1 - (p.value - min) / span),
    ]);

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');

    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const meanY = PAD + (H - PAD * 2) * (1 - (mean - min) / span);
    const base = document.createElementNS(SVG_NS, 'line');
    base.setAttribute('class', 'fit-spark-base');
    base.setAttribute('x1', PAD); base.setAttribute('x2', W - PAD);
    base.setAttribute('y1', meanY); base.setAttribute('y2', meanY);
    svg.appendChild(base);

    const d = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');

    if (xy.length > 1) {
      const area = document.createElementNS(SVG_NS, 'path');
      area.setAttribute('class', 'fit-spark-area');
      area.setAttribute('d', `${d} L${xy[xy.length - 1][0].toFixed(1)} ${H - PAD} L${xy[0][0].toFixed(1)} ${H - PAD} Z`);
      svg.appendChild(area);
    }

    const line = document.createElementNS(SVG_NS, 'path');
    line.setAttribute('class', 'fit-spark-line');
    line.setAttribute('d', d);
    svg.appendChild(line);

    const [lx, ly] = xy[xy.length - 1];
    const dot = document.createElementNS(SVG_NS, 'circle');
    dot.setAttribute('class', 'fit-spark-dot');
    dot.setAttribute('cx', lx); dot.setAttribute('cy', ly); dot.setAttribute('r', 2.5);
    svg.appendChild(dot);

    svg.setAttribute('aria-label',
      `${points.length} Werte, zuletzt ${NUM.format(points[points.length - 1].value)}`);
    return svg;
  }

  function renderCharts(series) {
    const host = el('fitness-charts');
    if (!host) return;
    host.innerHTML = '';

    const usable = Object.entries(series || {})
      .filter(([, pts]) => pts.length >= 2)
      .sort(([a], [b]) => a.localeCompare(b));

    if (!usable.length) {
      const empty = document.createElement('div');
      empty.className = 'fit-muted';
      empty.textContent = 'Noch zu wenig Verlaufsdaten für eine Kurve.';
      host.appendChild(empty);
      return;
    }

    for (const [name, points] of usable) {
      const card = document.createElement('div');
      card.className = 'fit-chart';

      const head = document.createElement('div');
      head.className = 'fit-chart-head';
      const label = document.createElement('span');
      label.className = 'fit-chart-name';
      label.textContent = name;
      label.title = name;
      const last = document.createElement('span');
      last.className = 'fit-chart-last';
      last.textContent = NUM.format(points[points.length - 1].value);
      head.appendChild(label);
      head.appendChild(last);

      card.appendChild(head);
      card.appendChild(sparkline(points));
      host.appendChild(card);
    }
  }

  // ---------------------------------------------------------------
  //  Notes
  // ---------------------------------------------------------------

  function renderNotes(payload) {
    const host = el('fitness-notes');
    const ttl = el('fitness-notes-ttl');
    if (ttl && payload?.ttl_days) ttl.textContent = `· laufen nach ${payload.ttl_days} Tagen ab`;
    if (!host) return;
    host.innerHTML = '';
    const notes = payload?.notes || [];
    if (!notes.length) {
      const li = document.createElement('li');
      li.className = 'fit-muted';
      li.textContent = 'Keine aktiven Notizen.';
      host.appendChild(li);
      return;
    }
    for (const n of notes.slice().reverse()) {
      const li = document.createElement('li');
      const d = document.createElement('span');
      d.className = 'fit-note-date';
      d.textContent = n.date;
      const t = document.createElement('span');
      t.textContent = n.text;
      li.appendChild(d);
      li.appendChild(t);
      host.appendChild(li);
    }
  }

  el('fitness-note-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = el('fitness-note-input');
    const btn = el('fitness-note-btn');
    const text = (input?.value || '').trim();
    if (!text) return;
    btn.disabled = true;
    try {
      await api('/api/fitness_coach/note', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: text }),
      });
      input.value = '';
      clearError();
      renderNotes(await api('/api/fitness_coach/notes'));
    } catch (err) {
      showError(`Notiz konnte nicht gespeichert werden: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------------------------------------------------------------
  //  Loading
  // ---------------------------------------------------------------

  async function refresh() {
    const [metrics, analysis, history, notes] = await Promise.allSettled([
      api('/api/fitness_coach/dashboard'),
      api('/api/fitness_coach/analysis'),
      api('/api/fitness_coach/history?days=30'),
      api('/api/fitness_coach/notes'),
    ]);

    if (metrics.status === 'fulfilled') renderMetrics(metrics.value);
    else showError(`Dashboard konnte nicht geladen werden: ${metrics.reason.message}`);

    if (analysis.status === 'fulfilled') {
      renderNotable(analysis.value.notable);
      const q = analysis.value.data_quality || {};
      const cov = el('fitness-coverage');
      if (cov) {
        cov.textContent = q.window_days
          ? `${q.days_with_any_data}/${q.window_days} Tage mit Daten`
          : '';
      }
    } else {
      renderNotable(null);
    }

    if (history.status === 'fulfilled') renderCharts(history.value.series);
    if (notes.status === 'fulfilled') renderNotes(notes.value);
  }

  fitnessBtn.addEventListener('click', () => {
    fitnessModal.classList.remove('hidden');
    clearError();
    refresh();
  });

  el('fitness-calc-btn')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = 'Berechne…';
    try {
      const result = await api('/api/fitness_coach/recalculate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionModule?.getCurrentSessionId?.() || undefined,
        }),
      });
      clearError();
      renderMetrics(result.metrics || result);
      renderNotable(result.notable);
    } catch (err) {
      showError(`Score konnte nicht berechnet werden: ${err.message}`);
    } finally {
      btn.innerHTML = original;
      btn.disabled = false;
    }
  });

  el('fitness-chat-btn')?.addEventListener('click', async () => {
    dismiss();
    // Flag before the session switch, not after: the old code set the flag and
    // then filled the input from a 250 ms timer, which raced the switch and
    // could drop the flag or type into the outgoing session.
    window.__isFitnessCoachNextTurn = true;
    try {
      if (sessionModule?.selectSession) await sessionModule.selectSession(null);
      else document.getElementById('sidebar-new-chat-btn')?.click();
    } catch (err) {
      console.error('[fitness] session switch failed', err);
    }
    window.__isFitnessCoachNextTurn = true;
    const chatInput = el('message');
    if (chatInput) {
      chatInput.value = 'Wie ist mein Zustand heute, und was soll ich trainieren?';
      chatInput.focus();
    }
  });
}
