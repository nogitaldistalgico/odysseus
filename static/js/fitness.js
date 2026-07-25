export function initFitnessModule(appElements, uiModule, sessionModule, chatModule) {
  const { el, API_BASE } = appElements;

  const fitnessBtn = el('tool-fitness-btn');
  const fitnessModal = el('fitness-modal');
  const closeBtn = el('close-fitness-modal');
  const chatBtn = el('fitness-chat-btn');

  if (!fitnessBtn || !fitnessModal) return;

  function dismiss() {
    fitnessModal.classList.add('hidden');
  }

  closeBtn.addEventListener('click', dismiss);
  
  // Close on outside click
  fitnessModal.addEventListener('click', (e) => {
    if (e.target === fitnessModal) dismiss();
  });

  // Fetch Dashboard
  async function fetchDashboard() {
    try {
      const res = await fetch(`${API_BASE}/api/fitness_coach/dashboard`);
      if (res.ok) {
        const data = await res.json();
        
        // Update Recovery
        if (data.recovery) {
          el('fitness-recovery-score').textContent = data.recovery.score;
          el('fitness-recovery-text').textContent = data.recovery.text;
        }
        
        // Update Condition
        if (data.condition) {
          el('fitness-condition-score').textContent = data.condition.score;
          el('fitness-condition-text').textContent = data.condition.text;
        }
        
        // Update Movement
        if (data.movement) {
          el('fitness-movement-score').textContent = `${data.movement.current} / ${data.movement.goal} ${data.movement.unit || 'kcal'}`;
          el('fitness-movement-text').textContent = data.movement.text;
        }
      }
    } catch (e) {
      console.error('Failed to fetch fitness dashboard', e);
    }
  }

  fitnessBtn.addEventListener('click', () => {
    fitnessModal.classList.remove('hidden');
    fetchDashboard();
  });

  const noteBtn = el('fitness-note-btn');
  if (noteBtn) {
    noteBtn.addEventListener('click', async () => {
      const noteText = prompt("Temporäre Notiz (z.B. gestern feiern, harter Umzug):");
      if (!noteText || !noteText.trim()) return;
      noteBtn.disabled = true;
      try {
        const r = await fetch(`${API_BASE}/api/fitness_coach/note`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: noteText.trim() })
        });
        if (!r.ok) {
          alert('Fehler beim Speichern der Notiz!');
        }
      } catch(e) {
        console.error('Note add error', e);
        alert('Fehler beim Speichern der Notiz!');
      }
      noteBtn.disabled = false;
    });
  }

  const calcBtn = el('fitness-calc-btn');
  if (calcBtn) {
    calcBtn.addEventListener('click', async () => {
      const originalIcon = calcBtn.innerHTML;
      calcBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="anim-spin" style="animation: spin 1s linear infinite;"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/></svg> Berechne...';
      calcBtn.disabled = true;

      try {
        const res = await fetch(`${API_BASE}/api/fitness_coach/recalculate`, {
          method: 'POST'
        });
        
        if (res.ok) {
            const result = await res.json();
            const data = result.metrics || result;
            if (el('fitness-recovery-score') && data.recovery) {
                el('fitness-recovery-score').textContent = data.recovery.score ?? '--';
                el('fitness-recovery-text').textContent = data.recovery.text ?? '';
            }
            if (el('fitness-condition-score') && data.condition) {
                el('fitness-condition-score').textContent = data.condition.score ?? '--';
                el('fitness-condition-text').textContent = data.condition.text ?? '';
            }
            if (el('fitness-movement-score') && data.movement) {
                el('fitness-movement-score').textContent = `${data.movement.current ?? 0} / ${data.movement.goal ?? 1000} ${data.movement.unit || 'kcal'}`;
                el('fitness-movement-text').textContent = data.movement.text ?? '';
            }
        } else {
            console.error("Recalculation error status:", res.status);
            alert("Fehler bei der Berechnung");
        }
      } catch(e) {
        console.error("Fehler bei der Berechnung:", e);
      } finally {
        calcBtn.innerHTML = originalIcon;
        calcBtn.disabled = false;
      }
    });
  }

  chatBtn.addEventListener('click', async () => {
    dismiss();
    
    // Create a new session first so we don't pollute the current chat
    if (sessionModule && sessionModule.selectSession) {
      await sessionModule.selectSession(null);
    } else {
      const newSessionBtn = document.getElementById('sidebar-new-chat-btn');
      if (newSessionBtn) newSessionBtn.click();
    }
    
    // Flag the next turn as fitness coach mode
    window.__isFitnessCoachNextTurn = true;
    
    // Auto-fill a message for the coach after a brief delay to let the new session initialize
    setTimeout(() => {
      const chatInput = el('message');
      if (chatInput) {
        chatInput.value = 'Hallo Fitness Coach!';
        chatInput.focus();
      }
    }, 250);
  });
}
