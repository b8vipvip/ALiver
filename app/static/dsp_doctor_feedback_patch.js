(() => {
  let progressTimer = null;
  let startedAt = 0;

  function targetButton(event) {
    const button = event.target?.closest?.('#dsp-env-apply, #dsp-env-check');
    return button || null;
  }

  function progressMessage(elapsed, apply) {
    let stage = '正在连接 Bridge 并读取当前状态';
    if (elapsed >= 5) stage = '正在检查三组虚拟声卡与 DSP 路由';
    if (elapsed >= 15) stage = apply ? '正在应用可自动修复的 ALiver 路由' : '正在整理检查结果';
    if (elapsed >= 30) stage = 'Windows 音频设备响应较慢，仍在等待';
    return `${stage}… 已等待 ${elapsed} 秒。最长约 53 秒，超时会自动结束并显示原因。`;
  }

  function stopProgress() {
    if (progressTimer) window.clearInterval(progressTimer);
    progressTimer = null;
  }

  document.addEventListener('click', event => {
    const button = targetButton(event);
    if (!button || button.disabled) return;
    stopProgress();
    startedAt = Date.now();
    const apply = button.id === 'dsp-env-apply';
    const box = document.getElementById('dsp-env-result');
    if (box) {
      box.className = 'diagnosis warn';
      box.textContent = progressMessage(0, apply);
    }

    progressTimer = window.setInterval(() => {
      const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
      const currentButton = document.getElementById(button.id);
      const currentBox = document.getElementById('dsp-env-result');
      if (!currentButton || !currentButton.disabled) {
        stopProgress();
        return;
      }
      if (elapsed >= 55) {
        stopProgress();
        if (currentBox) {
          currentBox.className = 'diagnosis bad';
          currentBox.textContent = '音频环境检查已超时。请确认 Bridge 仍保持连接，并查看服务端终端是否还有 database is locked 或 WebSocket 断线。';
        }
        return;
      }
      if (currentBox) {
        currentBox.className = 'diagnosis warn';
        currentBox.textContent = progressMessage(elapsed, apply);
      }
    }, 1000);
  }, true);

  window.addEventListener('beforeunload', stopProgress, { once: true });
})();
