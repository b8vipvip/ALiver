(() => {
  const EXPECTED_BRIDGE_VERSION = '0.11.1';
  const SERVER_VERSION = '0.15.1';
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
          `当前 Bridge 为 ${bridgeVersion}，请停止旧进程并启动 ${EXPECTED_BRIDGE_VERSION}。新版本增加服务端等待重试和数字人会话自动恢复。`,
        );
        setClass(warning, 'diagnosis bad');
      } else {
        setText(
          warning,
          `服务端 ${SERVER_VERSION} 与 Bridge ${bridgeVersion} 已匹配；窗口诊断、原生语音调音和会话自动恢复能力已启用。`,
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
