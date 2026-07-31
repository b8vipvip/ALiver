(() => {
  const EXPECTED_BRIDGE_VERSION = '0.10.3';
  const SERVER_VERSION = '0.14.3';

  function connectedBridge() {
    return (window.state?.bridges || []).find(item => item.connected) || null;
  }

  function applyVersionState() {
    const serverBadge = document.getElementById('live-debug-server-version');
    const bridgeBadge = document.getElementById('live-debug-bridge-version');
    const warning = document.getElementById('live-debug-version-warning');
    if (!serverBadge || !bridgeBadge || !warning) return;

    const bridge = connectedBridge();
    const bridgeVersion = String(bridge?.version || '未连接');
    serverBadge.textContent = `服务端 ${SERVER_VERSION}`;
    serverBadge.className = 'badge good';
    bridgeBadge.textContent = `Bridge ${bridgeVersion}`;
    bridgeBadge.className = `badge ${bridgeVersion === EXPECTED_BRIDGE_VERSION ? 'good' : 'warn'}`;

    if (!bridge) {
      warning.textContent = '没有在线 Windows Bridge。采集、VTube Studio、动作与口型验证暂时不能运行。';
      warning.className = 'diagnosis bad';
    } else if (bridgeVersion !== EXPECTED_BRIDGE_VERSION) {
      warning.textContent = `当前 Bridge 为 ${bridgeVersion}，请停止旧进程并启动 ${EXPECTED_BRIDGE_VERSION}。该版本会把 WGC 绑定到已验证的直播伴侣 HWND。`;
      warning.className = 'diagnosis bad';
    } else {
      warning.textContent = `服务端 ${SERVER_VERSION} 与 Bridge ${bridgeVersion} 已匹配；WGC 将优先使用准确 HWND，失败时仅回退到同一窗口的 PrintWindow 表面。`;
      warning.className = 'diagnosis good';
    }
  }

  function renderTargetDetails() {
    const status = document.getElementById('douyin-collector-status-json');
    const panel = document.getElementById('douyin-live-collector-panel');
    if (!status || !panel) return;
    let data;
    try {
      data = JSON.parse(status.textContent || '{}');
    } catch (_) {
      return;
    }
    const hwnd = data.wgc_target_hwnd_hex || (data.wgc_target_hwnd ? `0x${Number(data.wgc_target_hwnd).toString(16).toUpperCase()}` : '等待捕获');
    const mode = data.wgc_target_mode || 'window_hwnd';
    const source = data.capture_source || '尚未取得画面';
    let box = document.getElementById('wgc-hwnd-runtime');
    if (!box) {
      box = document.createElement('div');
      box.id = 'wgc-hwnd-runtime';
      box.className = 'diagnosis warn';
      const error = panel.querySelector('.diagnosis.bad');
      if (error) error.insertAdjacentElement('afterend', box);
      else panel.prepend(box);
    }
    box.textContent = `WGC 目标：${hwnd} · 选择方式：${mode} · 当前画面源：${source}`;
    box.className = `diagnosis ${source === 'windows_graphics_capture' || source === 'printwindow' ? 'good' : 'warn'}`;
  }

  function refresh() {
    applyVersionState();
    renderTargetDetails();
  }

  const observer = new MutationObserver(refresh);
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  window.setInterval(refresh, 700);
  refresh();
})();
