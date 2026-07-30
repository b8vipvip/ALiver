(() => {
  let installed = false;
  let lastDiagnosticPath = '';

  function selectedBridgeId() {
    return document.getElementById('douyin-visible-bridge')?.value
      || (state.bridges || []).find(item => item.connected)?.id
      || '';
  }

  async function command(name, payload = {}, timeout = 60) {
    const bridgeId = selectedBridgeId();
    if (!bridgeId) throw new Error('没有在线 Windows Bridge');
    return sendBridgeCommand(bridgeId, name, payload, timeout);
  }

  function captureLabel(value) {
    return ({
      printwindow: '窗口内容捕获（不受浏览器遮挡）',
      screen_visible: '前台屏幕兜底（直播伴侣必须无遮挡）',
      screen_region_clear: '屏幕兜底（仅要求 OCR 互动区域无遮挡）',
    })[value] || value || '尚未捕获';
  }

  function setMessage(text, bad = false) {
    const element = document.getElementById('douyin-capture-diagnostics-message');
    if (!element) return;
    element.className = `diagnosis ${bad ? 'bad' : 'ok'}`;
    element.textContent = text;
  }

  async function refreshPreview() {
    setMessage('正在直接读取直播伴侣窗口内容……');
    const result = await command('douyin.visible.preview', {}, 90);
    const windowImage = document.getElementById('douyin-capture-window-image');
    const regionImage = document.getElementById('douyin-capture-region-image');
    if (windowImage) {
      windowImage.src = result.window_image || '';
      windowImage.hidden = !result.window_image;
    }
    if (regionImage) {
      regionImage.src = result.region_image || '';
      regionImage.hidden = !result.region_image;
    }
    const meta = document.getElementById('douyin-capture-preview-meta');
    if (meta) {
      meta.textContent = `截图源：${captureLabel(result.capture_source)} · OCR 像素区域：${(result.last_region_pixels || []).join(', ') || '未知'} · ${formatTime(result.last_capture_at)}`;
    }
    setMessage(`已读取直播伴侣窗口。截图源：${captureLabel(result.capture_source)}。请确认右侧 OCR 裁剪图只包含“互动消息”区域。`);
  }

  async function runProbe() {
    setMessage('正在读取直播伴侣 UI Automation 可访问树……');
    const result = await command('douyin.visible.uia_probe', {}, 90);
    const target = document.getElementById('douyin-uia-probe-results');
    const rows = (result.controls || []).filter(item => item.in_ocr_region);
    if (target) {
      target.innerHTML = rows.slice(0, 120).map(item => `
        <div class="douyin-probe-row">
          <strong>${escapeHtml(item.text || '')}</strong>
          <small>${escapeHtml(item.control_type || '未知控件')} · ${escapeHtml(item.class_name || '')} · ${escapeHtml(item.automation_id || '')}</small>
        </div>
      `).join('') || '<p class="hint">互动区域没有暴露可访问文本，将继续使用窗口内容 OCR。</p>';
    }
    setMessage(`UIA 探针完成：整个窗口 ${Number(result.control_count || 0)} 个文本控件，互动区域 ${Number(result.region_control_count || 0)} 个。`);
  }

  async function exportDiagnostics() {
    setMessage('正在生成采集诊断包……');
    const result = await command('douyin.visible.export_diagnostics', {}, 120);
    const path = String(result.path || '');
    lastDiagnosticPath = path;
    const target = document.getElementById('douyin-diagnostics-path');
    if (target) target.textContent = path;
    const openButton = document.getElementById('douyin-open-diagnostics-folder');
    if (openButton) openButton.disabled = !path;
    try {
      if (path && navigator.clipboard) await navigator.clipboard.writeText(path);
    } catch (_) {}
    setMessage(`诊断包已生成：${path}${path ? '（路径已尝试复制）' : ''}`);
    toast('抖音采集诊断包已生成，可直接把 ZIP 文件上传到聊天中');
  }

  async function openDiagnosticsFolder() {
    const path = lastDiagnosticPath || document.getElementById('douyin-diagnostics-path')?.textContent || '';
    if (!path || path === '尚未导出') throw new Error('请先导出采集诊断包');
    const result = await command('douyin.visible.open_diagnostics_folder', { path }, 30);
    setMessage(`已打开诊断文件夹：${result.folder || path}`);
  }

  async function clearLocalHistory() {
    await command('douyin.visible.clear_local_history', {}, 30);
    setMessage('已清空 Bridge 本地采集计数、原始文本和事件显示。服务端历史记录不会被删除。');
    toast('已清空本地采集显示');
    document.getElementById('douyin-collector-refresh')?.click();
  }

  function bind(id, handler) {
    document.getElementById(id)?.addEventListener('click', () => {
      handler().catch(error => {
        setMessage(error.message, true);
        toast(error.message, true);
      });
    });
  }

  function install() {
    if (installed) return true;
    const panel = document.getElementById('douyin-live-collector-panel');
    const notes = panel?.querySelector('.douyin-collector-notes');
    if (!panel || !notes) return false;
    installed = true;

    const section = document.createElement('section');
    section.id = 'douyin-capture-diagnostics';
    section.innerHTML = `
      <div class="section-title douyin-capture-title">
        <div>
          <h3>窗口捕获预览与只读探针</h3>
          <p class="hint">先看“实际窗口截图”和“OCR 裁剪区”，再判断识别问题。不会扫描安装包、不会注入直播伴侣进程。</p>
        </div>
      </div>
      <div class="actions">
        <button id="douyin-capture-preview" type="button" class="secondary">查看实际截图</button>
        <button id="douyin-uia-probe" type="button" class="secondary">运行 UIA 探针</button>
        <button id="douyin-export-diagnostics" type="button" class="secondary">导出采集诊断包</button>
        <button id="douyin-clear-local-history" type="button" class="danger">清空本地测试显示</button>
      </div>
      <div id="douyin-capture-diagnostics-message" class="diagnosis warn">尚未获取窗口截图。屏幕兜底现在按 OCR 互动区域的实际遮挡判断，不要求直播伴侣必须是前台窗口。</div>
      <div class="douyin-capture-preview-grid">
        <figure>
          <figcaption>直播伴侣窗口实际捕获内容</figcaption>
          <img id="douyin-capture-window-image" alt="直播伴侣窗口截图" hidden>
        </figure>
        <figure>
          <figcaption>送入 OCR 的互动区域</figcaption>
          <img id="douyin-capture-region-image" alt="OCR 互动区域截图" hidden>
        </figure>
      </div>
      <p id="douyin-capture-preview-meta" class="hint">尚未捕获。</p>
      <details>
        <summary>UIA 探针结果</summary>
        <div id="douyin-uia-probe-results" class="douyin-uia-probe-results"><p class="hint">尚未运行探针。</p></div>
      </details>
      <details>
        <summary>诊断包位置</summary>
        <div class="douyin-diagnostics-location">
          <code id="douyin-diagnostics-path" class="douyin-diagnostics-path">尚未导出</code>
          <button id="douyin-open-diagnostics-folder" type="button" class="secondary" disabled>打开文件夹</button>
        </div>
      </details>
    `;
    notes.insertAdjacentElement('beforebegin', section);

    bind('douyin-capture-preview', refreshPreview);
    bind('douyin-uia-probe', runProbe);
    bind('douyin-export-diagnostics', exportDiagnostics);
    bind('douyin-open-diagnostics-folder', openDiagnosticsFolder);
    bind('douyin-clear-local-history', clearLocalHistory);
    return true;
  }

  if (!install()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (install() || attempts > 120) clearInterval(timer);
    }, 250);
  }
})();
