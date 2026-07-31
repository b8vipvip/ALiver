(() => {
  const EXPECTED_BRIDGE_VERSION = '0.10.4';
  const SERVER_VERSION = '0.14.4';
  let applying = false;
  let pending = false;
  let statusObserver = null;

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
    if (!serverBadge || !bridgeBadge || !warning) return false;

    const bridge = connectedBridge();
    const bridgeVersion = String(bridge?.version || '未连接');
    applying = true;
    try {
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
          `当前 Bridge 为 ${bridgeVersion}，请停止旧进程并启动 ${EXPECTED_BRIDGE_VERSION}。新版本会分离开播前检查和开播后真实互动验证。`,
        );
        setClass(warning, 'diagnosis bad');
      } else {
        setText(
          warning,
          `服务端 ${SERVER_VERSION} 与 Bridge ${bridgeVersion} 已匹配；可先运行开播前检查，再在开播后验证真实互动闭环。`,
        );
        setClass(warning, 'diagnosis good');
      }
    } finally {
      applying = false;
    }
    return true;
  }

  function scheduleApply() {
    if (applying || pending) return;
    pending = true;
    queueMicrotask(() => {
      pending = false;
      applyVersionState();
    });
  }

  function bindStatusObserver() {
    const elements = [
      document.getElementById('live-debug-server-version'),
      document.getElementById('live-debug-bridge-version'),
      document.getElementById('live-debug-version-warning'),
    ];
    if (elements.some(element => !element)) return false;

    applyVersionState();
    statusObserver?.disconnect();
    statusObserver = new MutationObserver(scheduleApply);
    for (const element of elements) {
      statusObserver.observe(element, {
        attributes: true,
        attributeFilter: ['class'],
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
    return true;
  }

  function start() {
    if (bindStatusObserver()) return;
    const mountObserver = new MutationObserver(() => {
      if (bindStatusObserver()) mountObserver.disconnect();
    });
    mountObserver.observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
