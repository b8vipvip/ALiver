(() => {
  const EXPECTED_BRIDGE_VERSION = '0.10.3';
  const SERVER_VERSION = '0.14.3';

  function connectedBridge() {
    const appState = typeof state !== 'undefined' ? state : window.state;
    return (appState?.bridges || []).find(item => item.connected) || null;
  }

  function setText(element, value) {
    if (element && element.textContent !== value) element.textContent = value;
  }

  function setClass(element, value) {
    if (element && element.className !== value) element.className = value;
  }

  function applyVersionState() {
    const serverBadge = document.getElementById('live-debug-server-version');
    const bridgeBadge = document.getElementById('live-debug-bridge-version');
    const warning = document.getElementById('live-debug-version-warning');
    if (!serverBadge || !bridgeBadge || !warning) return;

    const bridge = connectedBridge();
    const bridgeVersion = String(bridge?.version || '未连接');
    setText(serverBadge, `服务端 ${SERVER_VERSION}`);
    setClass(serverBadge, 'badge good');
    setText(bridgeBadge, `Bridge ${bridgeVersion}`);
    setClass(bridgeBadge, `badge ${bridgeVersion === EXPECTED_BRIDGE_VERSION ? 'good' : 'warn'}`);

    if (!bridge) {
      setText(warning, '没有在线 Windows Bridge。采集、VTube Studio、动作与口型验证暂时不能运行。');
      setClass(warning, 'diagnosis bad');
    } else if (bridgeVersion !== EXPECTED_BRIDGE_VERSION) {
      setText(
        warning,
        `当前 Bridge 为 ${bridgeVersion}，请停止旧进程并启动 ${EXPECTED_BRIDGE_VERSION}。该版本会把 WGC 绑定到已验证的直播伴侣 HWND。`,
      );
      setClass(warning, 'diagnosis bad');
    } else {
      setText(
        warning,
        `服务端 ${SERVER_VERSION} 与 Bridge ${bridgeVersion} 已匹配；WGC 将优先使用准确 HWND，失败时仅回退到同一窗口的 PrintWindow 表面。`,
      );
      setClass(warning, 'diagnosis good');
    }
  }

  window.setInterval(applyVersionState, 500);
  applyVersionState();
})();
