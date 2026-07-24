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

  chatBtn.addEventListener('click', async () => {
    dismiss();
    
    // Flag the next turn as fitness coach mode
    window.__isFitnessCoachNextTurn = true;
    
    // Auto-fill a message for the coach
    const chatInput = el('message');
    if (chatInput) {
      chatInput.value = 'Hallo Fitness Coach! Wie sehen meine aktuellen Werte aus?';
      chatInput.focus();
    }
  });
}
