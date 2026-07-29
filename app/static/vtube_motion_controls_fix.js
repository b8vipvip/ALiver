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

  window.setInterval(() => {
    pending.forEach((value, id) => {
      const element = document.getElementById(id);
      if (element && element.value !== value) element.value = value;
    });
  }, 100);
})();
