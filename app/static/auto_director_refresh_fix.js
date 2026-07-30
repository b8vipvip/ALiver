(() => {
  function cardKey(card) {
    const title = card.querySelector('strong')?.textContent || '';
    const meta = card.querySelector('.meta')?.textContent || '';
    const reason = card.querySelector('.auto-event-content')?.textContent || '';
    return `${title}|${meta}|${reason}`;
  }

  function install() {
    const original = window.renderProfessionalDecisions;
    if (typeof original !== 'function') return false;
    if (original.__aliverPreservesDecisionDetails) return true;

    function renderProfessionalDecisionsPreservingDetails(...args) {
      const before = document.getElementById('professional-decision-list');
      const openKeys = new Set();
      let scrollTop = 0;
      if (before) {
        scrollTop = before.scrollTop;
        before.querySelectorAll('.professional-decision-card').forEach(card => {
          if (card.querySelector('details[open]')) openKeys.add(cardKey(card));
        });
      }

      const result = original.apply(this, args);
      const after = document.getElementById('professional-decision-list');
      if (after) {
        after.querySelectorAll('.professional-decision-card').forEach(card => {
          const details = card.querySelector('details');
          if (details && openKeys.has(cardKey(card))) details.open = true;
        });
        after.scrollTop = scrollTop;
      }
      return result;
    }

    renderProfessionalDecisionsPreservingDetails.__aliverPreservesDecisionDetails = true;
    window.renderProfessionalDecisions = renderProfessionalDecisionsPreservingDetails;
    return true;
  }

  if (install()) return;
  let attempts = 0;
  const timer = setInterval(() => {
    attempts += 1;
    if (install() || attempts >= 100) clearInterval(timer);
  }, 100);
})();
