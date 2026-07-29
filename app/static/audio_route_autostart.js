(() => {
  const initializedBridges = new Set();
  const inFlightBridges = new Set();
  let successToastShown = false;

  function routeStatusElement() {
    let element = document.getElementById('audio-route-autostart-status');
    if (element) return element;
    const intro = document.querySelector('#tab-audio .route-intro');
    if (!intro) return null;
    element = document.createElement('div');
    element.id = 'audio-route-autostart-status';
    element.className = 'diagnosis warn';
    element.textContent = '等待在线 Bridge，随后自动扫描并配置 GPT_IN / GPT_OUT。';
    intro.appendChild(element);
    return element;
  }

  function setRouteMessage(message, kind = 'warn') {
    const element = routeStatusElement();
    if (!element) return;
    element.textContent = message;
    element.className = `diagnosis ${kind}`;
  }

  function routeIsReady(data) {
    return Boolean(data?.routes?.ready);
  }

  function shouldRenderBridge(bridgeId) {
    const select = document.getElementById('audio-bridge');
    if (!select) return false;
    if (!select.value) select.value = bridgeId;
    return select.value === bridgeId;
  }

  async function initializeBridgeRoutes(bridgeId) {
    if (!bridgeId || initializedBridges.has(bridgeId) || inFlightBridges.has(bridgeId)) return;
    inFlightBridges.add(bridgeId);
    try {
      setRouteMessage('正在自动扫描虚拟声卡和已保存路由…');
      let scan = await sendBridgeCommand(bridgeId, 'audio.routes.scan', {}, 30);
      if (shouldRenderBridge(bridgeId)) renderAudioScan(scan);

      if (!routeIsReady(scan)) {
        setRouteMessage('尚未形成双通道路由，正在自动选择两组隔离的虚拟声卡并保存…');
        await sendBridgeCommand(bridgeId, 'audio.routes.auto', {}, 30);
        scan = await sendBridgeCommand(bridgeId, 'audio.routes.scan', {}, 30);
        if (shouldRenderBridge(bridgeId)) renderAudioScan(scan);
      }

      if (!routeIsReady(scan)) {
        const warnings = scan?.routes?.warnings || [];
        throw new Error(warnings[0] || '未找到两组可用于 GPT_IN / GPT_OUT 的完整虚拟声卡。');
      }

      initializedBridges.add(bridgeId);
      setRouteMessage('虚拟声卡已自动扫描；GPT_IN / GPT_OUT 路由已读取并确认可用。', 'good');
      if (!successToastShown) {
        successToastShown = true;
        toast('音频路由已自动扫描并完成默认配置');
      }
    } catch (error) {
      setRouteMessage(`自动配置暂未完成：${error.message}。Bridge 在线后会自动重试，也可使用上方按钮手动配置。`, 'bad');
    } finally {
      inFlightBridges.delete(bridgeId);
    }
  }

  async function initializeOnlineBridges() {
    const online = (state?.bridges || []).filter(bridge => bridge.connected);
    if (!online.length) {
      setRouteMessage('尚未发现在线 Bridge；Bridge 上线后将自动扫描并配置虚拟声卡。');
      return;
    }
    const selected = document.getElementById('audio-bridge');
    if (selected && !selected.value && online.length === 1) selected.value = online[0].id;
    await Promise.all(online.map(bridge => initializeBridgeRoutes(bridge.id)));
  }

  function installBridgeRefreshHook() {
    if (typeof loadBridges !== 'function' || loadBridges.__audioRouteAutostart) return;
    const original = loadBridges;
    const wrapped = async (...args) => {
      const result = await original(...args);
      initializeOnlineBridges().catch(() => {});
      return result;
    };
    wrapped.__audioRouteAutostart = true;
    loadBridges = wrapped;
  }

  function startAudioAutostart() {
    if (
      typeof state === 'undefined'
      || typeof sendBridgeCommand !== 'function'
      || typeof renderAudioScan !== 'function'
      || !document.getElementById('tab-audio')
    ) {
      setTimeout(startAudioAutostart, 150);
      return;
    }
    routeStatusElement();
    installBridgeRefreshHook();
    initializeOnlineBridges().catch(() => {});
    setInterval(() => initializeOnlineBridges().catch(() => {}), 5000);
  }

  startAudioAutostart();
})();

(() => {
  function mappingPanel() {
    return document.getElementById('vtube-semantic-mapping-editor');
  }

  function addActionGuide(panel) {
    if (panel.querySelector('#vtube-action-semantic-guide')) return;
    const guide = document.createElement('div');
    guide.id = 'vtube-action-semantic-guide';
    guide.className = 'diagnosis warn';
    guide.innerHTML = [
      '<strong>动作名称只是映射标签，不会自动生成动作。</strong>',
      '“挥手”按钮只会触发下拉框中选定的 VTube Studio 热键。',
      '请先在右侧逐个触发 My Animation 1/2/3，确认真实动作后再映射。',
      '当前模型没有挥手、开心等动作时，需要在 VTube Studio 的热键编辑器中绑定合适的 motion3/表情，或更换包含这些动作的模型。',
    ].join(' ');
    const fields = panel.querySelector('#vtube-mapping-fields');
    panel.insertBefore(guide, fields || panel.firstChild);

    const duplicate = document.createElement('div');
    duplicate.id = 'vtube-action-duplicate-warning';
    duplicate.className = 'diagnosis warn';
    duplicate.textContent = '建议一个实际热键只映射给一个语义动作。';
    const status = panel.querySelector('#vtube-mapping-status');
    panel.insertBefore(duplicate, status || null);
  }

  function updateDuplicateWarning(panel) {
    const warning = panel.querySelector('#vtube-action-duplicate-warning');
    if (!warning) return;
    const grouped = new Map();
    panel.querySelectorAll('[data-vtube-map]').forEach(select => {
      const value = select.value;
      if (!value) return;
      const labels = grouped.get(value) || [];
      labels.push(select.closest('label')?.childNodes?.[0]?.textContent?.trim() || select.dataset.vtubeMap);
      grouped.set(value, labels);
    });
    const duplicates = [...grouped.values()].filter(labels => labels.length > 1);
    if (duplicates.length) {
      warning.textContent = `发现重复映射：${duplicates.map(labels => labels.join('、')).join('；')}。这些按钮会执行同一个动作，并不会表现出不同语义。`;
      warning.className = 'diagnosis bad';
    } else {
      warning.textContent = '未发现重复动作映射。未配置的语义按钮保持不可用即可。';
      warning.className = 'diagnosis good';
    }
  }

  function enhanceAutoFill(panel) {
    const button = panel.querySelector('#vtube-mapping-auto');
    if (!button || button.dataset.semanticGuided === '1') return;
    button.dataset.semanticGuided = '1';
    button.textContent = '按顺序临时填充（仅测试）';
    button.title = '只用于快速测试：动作1=挥手、动作2=开心、动作3=思考。真实语义必须人工确认。';
    button.addEventListener('click', () => {
      panel.querySelectorAll('[data-vtube-map]').forEach(select => { select.value = ''; });
      setTimeout(() => updateDuplicateWarning(panel), 0);
    }, true);
  }

  function enhancePanel() {
    const panel = mappingPanel();
    if (!panel) return false;
    addActionGuide(panel);
    enhanceAutoFill(panel);
    if (panel.dataset.semanticListener !== '1') {
      panel.dataset.semanticListener = '1';
      panel.addEventListener('change', event => {
        if (event.target.matches('[data-vtube-map]')) updateDuplicateWarning(panel);
      });
    }
    updateDuplicateWarning(panel);
    return true;
  }

  function startActionGuide() {
    if (!enhancePanel()) {
      setTimeout(startActionGuide, 250);
      return;
    }
    const root = document.getElementById('tab-avatar-debug') || document.body;
    new MutationObserver(() => enhancePanel()).observe(root, { childList: true, subtree: true });
  }

  startActionGuide();
})();
