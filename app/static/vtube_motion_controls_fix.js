(() => {
  const CONTROL_IDS = new Set([
    'vtube-motion-preset',
    'vtube-motion-idle',
    'vtube-motion-talking',
    'vtube-motion-threshold',
    'vtube-motion-fps-input',
  ]);
  const pending = new Map();

  document.addEventListener('input', event => {
    if (!CONTROL_IDS.has(event.target?.id)) return;
    pending.set(event.target.id, event.target.value);
  }, true);

  document.addEventListener('change', event => {
    if (!CONTROL_IDS.has(event.target?.id)) return;
    pending.set(event.target.id, event.target.value);
  }, true);

  document.addEventListener('click', event => {
    if (!event.target?.closest('#vtube-motion-enable, #vtube-motion-disable')) return;
    window.setTimeout(() => pending.clear(), 3500);
  }, true);

  function repairProceduralActionLabel() {
    const box = document.getElementById('vtube-action-result');
    if (!box || !box.textContent.includes('undefined')) return;
    const pre = document.getElementById('vtube-live-json');
    try {
      const data = JSON.parse(pre?.textContent || '{}');
      const action = data?.runtime?.last_action || data?.runtime?.motion?.transient_action;
      box.textContent = `已触发程序化动作：${action || '自然动作'}`;
      box.className = 'diagnosis good';
    } catch (_) {
      box.textContent = '已触发程序化自然动作';
      box.className = 'diagnosis good';
    }
  }

  window.setInterval(() => {
    pending.forEach((value, id) => {
      const element = document.getElementById(id);
      if (element && element.value !== value) element.value = value;
    });
    repairProceduralActionLabel();
  }, 100);
})();
