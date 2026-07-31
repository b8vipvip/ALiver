(() => {
  function enhance() {
    const workspace = document.getElementById('live-debug-staged-validation');
    if (!workspace) return false;

    const cards = [...workspace.querySelectorAll('.validation-phase-card')];
    const preflight = cards.find(card => card.querySelector('h3')?.textContent.includes('开播前'));
    const live = cards.find(card => card.querySelector('h3')?.textContent.includes('开播后'));

    if (preflight && preflight.dataset.recoveryUi !== '1') {
      preflight.dataset.recoveryUi = '1';
      const hint = preflight.querySelector('p.hint');
      if (hint) {
        hint.textContent = '检查窗口、三级采集、截图、双虚拟声卡和数字人。模拟欢迎会临时恢复停止、暂停或紧急状态的测试导演，完成后恢复原状态。';
      }
    }

    if (live && live.dataset.recoveryUi !== '1') {
      live.dataset.recoveryUi = '1';
      const hint = live.querySelector('p.hint');
      if (hint) {
        hint.textContent = '开始后请用另一账号进入或评论。采集器若已停止，系统会按当前保存配置自动启动并确认首次扫描，再等待真实互动。';
      }
      const actions = live.querySelector('.actions');
      if (actions && !document.getElementById('live-debug-auto-recovery-note')) {
        const note = document.createElement('span');
        note.id = 'live-debug-auto-recovery-note';
        note.className = 'hint';
        note.textContent = '自动处理：采集器启动 · 可见窗口选择 · 新旧截图隔离';
        actions.insertAdjacentElement('afterend', note);
      }
    }
    return true;
  }

  function start() {
    enhance();
    const observer = new MutationObserver(enhance);
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
