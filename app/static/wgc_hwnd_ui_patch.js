(() => {
  const EXPECTED_BRIDGE_VERSION = '0.12.2';
  const SERVER_VERSION = '0.16.4';
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
        setText(warning, '没有在线 Windows Bridge。采集、VTube Studio、动作、口型与本机音频暂时不能运行。');
        setClass(warning, 'diagnosis bad');
      } else if (bridgeVersion !== EXPECTED_BRIDGE_VERSION) {
        setText(
          warning,
          `当前 Bridge 为 ${bridgeVersion}，请停止旧进程并启动 ${EXPECTED_BRIDGE_VERSION}。新版本支持连续颗粒变调和可命名的声音预设库。`,
        );
        setClass(warning, 'diagnosis bad');
      } else {
        setText(
          warning,
          `服务端 ${SERVER_VERSION} 与 Bridge ${bridgeVersion} 已匹配；浏览器 ChatGPT 策划、连续 DSP、声音预设库和会话恢复能力已启用。`,
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

  function installObserver() {
    if (statusObserver || !document.body) return;
    statusObserver = new MutationObserver(scheduleApply);
    statusObserver.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function install() {
    installObserver();
    applyVersionState();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
})();
