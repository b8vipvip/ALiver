(() => {
  let running = false;
  let lastPath = '';

  function escape(value) {
    return typeof escapeHtml === 'function'
      ? escapeHtml(String(value ?? ''))
      : String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  function activeSession() {
    const active = new Set(['starting', 'active', 'running', 'ready', 'awaiting_manual', 'reconnecting']);
    return (state.sessions || []).find(item => active.has(item.status)) || null;
  }

  function bridgeId() {
    return document.getElementById('douyin-visible-bridge')?.value
      || activeSession()?.bridge_id
      || (state.bridges || []).find(item => item.connected)?.id
      || '';
  }

  function label(name) {
    return ({
      'collector.window_permissions': '直播伴侣窗口与权限',
      'collector.three_channels': '三级互动采集',
      'collector.wgc_preview': 'WGC 实际窗口帧',
      'collector.diagnostics': '采集诊断包',
      'avatar.connection_model': 'VTube Studio 连接与模型',
      'avatar.motion_capabilities': '动作参数能力',
      'avatar.actions': '动作连续校验',
      'avatar.mouth_audio_route': '口型与虚拟音频链路',
    })[name] || name || '未知步骤';
  }

  function render(result) {
    const target = document.getElementById('live-debug-validation-placeholder');
    if (!target) return;
    const rows = [
      ...(result?.report?.collector_steps || []),
      ...(result?.report?.avatar?.steps || []),
    ];
    const summary = result?.summary || {};
    lastPath = String(result?.path || '');
    target.innerHTML = `
      <div class="diagnosis ${Number(summary.failed || 0) ? 'warn' : 'good'}">
        验证完成：${Number(summary.passed || 0)} 项通过，${Number(summary.failed || 0)} 项失败；总体结果 ${escape(summary.overall || 'unknown')}。
      </div>
      <div class="aliver-validation-grid">
        ${rows.map(item => `
          <article class="${item.ok ? 'validation-pass' : 'validation-fail'}">
            <span>${escape(label(item.name))}</span>
            <strong>${item.ok ? '通过' : item.status === 'skipped' ? '跳过' : item.status === 'missing' ? '缺少条件' : '失败'}</strong>
            <small>${escape(item.error || `${Number(item.elapsed_ms || 0)} ms`)}</small>
          </article>
        `).join('') || '<p class="hint">验证任务没有返回步骤。</p>'}
      </div>
      <div class="aliver-validation-path-row">
        <code>${escape(lastPath || '未生成诊断包')}</code>
        <button id="live-debug-open-validation" type="button" class="secondary" ${lastPath ? '' : 'disabled'}>打开诊断包文件夹</button>
      </div>
      <details>
        <summary>完整验证 JSON</summary>
        <pre>${escape(JSON.stringify(result, null, 2))}</pre>
      </details>
    `;
    document.getElementById('live-debug-open-validation')?.addEventListener('click', () => {
      openFolder().catch(error => toast(error.message, true));
    });
  }

  async function run() {
    if (running) throw new Error('完整验证正在运行，请等待当前任务结束');
    const id = bridgeId();
    if (!id) throw new Error('没有在线 Windows Bridge');
    const session = activeSession();
    const button = document.getElementById('live-debug-full-validation');
    const target = document.getElementById('live-debug-validation-placeholder');
    running = true;
    if (button) {
      button.disabled = true;
      button.textContent = '正在自动验证…';
    }
    if (target) {
      target.innerHTML = '<div class="diagnosis warn">正在连续验证采集、窗口捕获、权限、数字人动作、虚拟音频和口型参数。任一步失败都不会中断后续步骤。</div>';
    }
    try {
      const result = await sendBridgeCommand(
        id,
        'aliver.full_validation',
        {
          session_id: session?.id || null,
          test_actions: true,
          test_mouth: true,
          skip_collector: false,
          skip_avatar: false,
        },
        240,
      );
      render(result);
      toast(`完整验证结束：${Number(result.summary?.passed || 0)} 项通过，${Number(result.summary?.failed || 0)} 项失败`);
      document.getElementById('avatar-debug-refresh')?.click();
      document.getElementById('douyin-collector-refresh')?.click();
      return result;
    } finally {
      running = false;
      if (button) {
        button.disabled = false;
        button.textContent = '一键完整验证并导出';
      }
    }
  }

  async function openFolder() {
    const id = bridgeId();
    if (!id) throw new Error('没有在线 Windows Bridge');
    if (!lastPath) throw new Error('请先运行一次完整验证');
    await sendBridgeCommand(id, 'douyin.visible.open_diagnostics_folder', { path: lastPath }, 30);
  }

  function bind() {
    const button = document.getElementById('live-debug-full-validation');
    if (!button || button.dataset.directValidationBound === '1') return false;
    button.dataset.directValidationBound = '1';
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      run().catch(error => {
        const target = document.getElementById('live-debug-validation-placeholder');
        if (target) target.innerHTML = `<div class="diagnosis bad">${escape(error.message)}</div>`;
        toast(error.message, true);
      });
    }, true);
    return true;
  }

  window.runAliverFullValidation = run;
  if (!bind()) {
    const timer = window.setInterval(() => {
      if (bind()) window.clearInterval(timer);
    }, 200);
  }
})();
