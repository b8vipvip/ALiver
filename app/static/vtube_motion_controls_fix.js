(() => {
  const CONTROL_IDS = new Set([
    'vtube-motion-preset',
    'vtube-motion-idle',
    'vtube-motion-talking',
    'vtube-motion-threshold',
    'vtube-motion-fps-input',
  ]);
  const guarded = new WeakSet();
  const dirty = new WeakMap();

  function nativeDescriptor(element) {
    const prototype = element instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
    return Object.getOwnPropertyDescriptor(prototype, 'value');
  }

  function guardControl(element) {
    if (!element || guarded.has(element)) return;
    const descriptor = nativeDescriptor(element);
    if (!descriptor?.get || !descriptor?.set) return;
    guarded.add(element);
    dirty.set(element, false);

    Object.defineProperty(element, 'value', {
      configurable: true,
      enumerable: descriptor.enumerable,
      get() {
        return descriptor.get.call(this);
      },
      set(nextValue) {
        const userEditing = dirty.get(this) || document.activeElement === this;
        if (userEditing) return;
        descriptor.set.call(this, nextValue);
      },
    });

    const markDirty = () => dirty.set(element, true);
    element.addEventListener('pointerdown', markDirty, true);
    element.addEventListener('keydown', markDirty, true);
    element.addEventListener('input', markDirty, true);
    element.addEventListener('change', markDirty, true);
  }

  function installGuards() {
    CONTROL_IDS.forEach(id => guardControl(document.getElementById(id)));
  }

  function releaseGuardsSoon() {
    window.setTimeout(() => {
      CONTROL_IDS.forEach(id => {
        const element = document.getElementById(id);
        if (element) dirty.set(element, false);
      });
    }, 2500);
  }

  document.addEventListener('click', event => {
    if (event.target?.closest('#vtube-motion-enable, #vtube-motion-disable')) {
      releaseGuardsSoon();
    }
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
    installGuards();
    repairProceduralActionLabel();
  }, 120);
})();
