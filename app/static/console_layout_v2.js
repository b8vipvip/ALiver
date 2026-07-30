(() => {
  const VERSION = '0.14.2';
  const EXPECTED_BRIDGE_VERSION = '0.10.2';

  function activateTab(name, button) {
    document.querySelectorAll('.tabs button').forEach(item => item.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(item => item.classList.remove('active'));
    const panel = document.getElementById(`tab-${name}`);
    if (!panel) return;
    button?.classList.add('active');
    panel.classList.add('active');
    if (name === 'audio' && typeof pollGptOutStatus === 'function') pollGptOutStatus();
    window.dispatchEvent(new CustomEvent('aliver:tabchange', { detail: { name } }));
  }

  function ensureLiveDebugWorkspace() {
    const tabs = document.querySelector('.tabs');
    if (!tabs) return null;

    let button = tabs.querySelector('[data-tab="simli-tuning"]');
    if (!button) {
      button = document.createElement('button');
      button.type = 'button';
      button.dataset.tab = 'simli-tuning';
      button.textContent = '直播调试';
      const logsButton = tabs.querySelector('[data-tab="logs"]');
      tabs.insertBefore(button, logsButton || null);
      button.addEventListener('click', () => activateTab('simli-tuning', button));
    }

    let panel = document.getElementById('tab-simli-tuning');
    if (!panel) {
      panel = document.createElement('section');
      panel.id = 'tab-simli-tuning';
      panel.className = 'tab-panel live-debug-page';
      panel.innerHTML = `
        <header class="page-heading live-debug-heading">
          <div>
            <span class="page-kicker">LIVE DIAGNOSTICS</span>
            <h2>直播调试中心</h2>
            <p>集中查看抖音互动采集、窗口捕获、数字人会话、动作、口型、音频链路与自动验证结果，不再挤在导演配置页面。</p>
          </div>
          <div class="page-heading-actions">
            <span id="live-debug-server-version" class="badge warn">服务端检查中</span>
            <span id="live-debug-bridge-version" class="badge warn">Bridge 检查中</span>
          </div>
        </header>
        <article class="panel live-debug-command-bar">
          <div class="section-title">
            <div>
              <h2>一键现场验证</h2>
              <p class="hint">自动连续检查窗口权限、三级采集、WGC 截图、VTube Studio 模型、动作和口型；单项失败不会中断，结束后统一导出 ZIP。</p>
            </div>
            <div class="actions">
              <button id="live-debug-full-validation" type="button">一键完整验证并导出</button>
              <button id="live-debug-refresh-all" type="button" class="secondary">刷新全部状态</button>
            </div>
          </div>
          <div id="live-debug-version-warning" class="diagnosis warn">正在读取服务端与 Bridge 版本。</div>
          <div id="live-debug-validation-placeholder" class="live-debug-validation-placeholder">
            <p class="hint">完整验证模块加载后，本区域会同步显示通过项、失败项和诊断包路径。</p>
          </div>
        </article>
        <article class="panel simli-tuning-hero">
          <div class="section-title">
            <div>
              <span class="page-kicker">AVATAR</span>
              <h2>数字人会话与参数调试</h2>
              <p class="hint">自动跟随最近活动会话，读取当前模型、动作、热键、口型参数和运行状态。</p>
            </div>
          </div>
        </article>
        <div id="live-debug-collector-host" class="live-debug-stack"></div>
      `;
      const logsPanel = document.getElementById('tab-logs');
      logsPanel?.parentElement?.insertBefore(panel, logsPanel);
    }

    button.textContent = '直播调试';
    return panel;
  }

  function mergeDirectorWorkspace() {
    const tabs = document.querySelector('.tabs');
    const autoButton = tabs?.querySelector('[data-tab="auto-director"]');
    autoButton?.remove();

    const director = document.getElementById('tab-director');
    const auto = document.getElementById('tab-auto-director');
    if (!director || !auto) return;

    if (!document.getElementById('director-page-heading')) {
      const heading = document.createElement('header');
      heading.id = 'director-page-heading';
      heading.className = 'page-heading';
      heading.innerHTML = `
        <div>
          <span class="page-kicker">DIRECTOR</span>
          <h2>导演中心</h2>
          <p>人工指令、专业自动导演、节目单、互动决策和命令队列统一在一个工作区中。</p>
        </div>
        <nav class="section-jump-nav">
          <button type="button" class="secondary" data-scroll-target="director-manual-section">人工导演</button>
          <button type="button" class="secondary" data-scroll-target="tab-auto-director">自动导演</button>
        </nav>
      `;
      director.prepend(heading);
    }

    const firstDirectorPanel = director.querySelector('.director-intro');
    if (firstDirectorPanel && !document.getElementById('director-manual-section')) {
      const marker = document.createElement('div');
      marker.id = 'director-manual-section';
      marker.className = 'workspace-section-heading';
      marker.innerHTML = '<span>01</span><div><h3>人工导演与命令队列</h3><p>临时干预、口播提示和命令执行记录。</p></div>';
      firstDirectorPanel.before(marker);
    }

    auto.classList.remove('tab-panel');
    auto.classList.add('director-subspace', 'active');
    if (auto.parentElement !== director) director.appendChild(auto);
    if (!auto.querySelector(':scope > .workspace-section-heading')) {
      const marker = document.createElement('div');
      marker.className = 'workspace-section-heading';
      marker.innerHTML = '<span>02</span><div><h3>专业自动导演</h3><p>节目单、互动筛选、节奏控制、动作联动与事件队列。</p></div>';
      auto.prepend(marker);
    }
  }

  function addPageHeading(panelId, kicker, title, description) {
    const panel = document.getElementById(panelId);
    if (!panel || panel.querySelector(':scope > .page-heading')) return;
    const heading = document.createElement('header');
    heading.className = 'page-heading';
    heading.innerHTML = `
      <div>
        <span class="page-kicker">${kicker}</span>
        <h2>${title}</h2>
        <p>${description}</p>
      </div>
    `;
    panel.prepend(heading);
  }

  function optimizePageLayouts() {
    addPageHeading('tab-providers', 'PROVIDERS', '数字人供应商', '新增、修改、测试和维护数字人连接配置。编辑区与供应商列表明确分栏。');
    addPageHeading('tab-sessions', 'SESSIONS', '数字人会话', '启动、命名、停止、重启和管理本地或云端数字人会话。');
    addPageHeading('tab-bridges', 'WINDOWS BRIDGE', '本地执行节点', '查看在线状态、版本、能力、心跳和本机执行信息。');
    addPageHeading('tab-audio', 'AUDIO ROUTING', '音频路由', '配置 GPT_IN、GPT_OUT、虚拟声卡配对并完成真实人声与回放测试。');
    addPageHeading('tab-logs', 'RUNTIME LOGS', '运行日志', '集中查看错误、延迟、会话和 Bridge 运行记录。');

    for (const id of ['tab-providers', 'tab-sessions']) {
      const grid = document.querySelector(`#${id} > .grid.two`);
      if (!grid) continue;
      grid.classList.add('workspace-split');
      const panels = grid.querySelectorAll(':scope > .panel');
      panels[0]?.classList.add('workspace-editor');
      panels[1]?.classList.add('workspace-results');
    }

    document.getElementById('provider-list')?.classList.add('structured-list');
    document.getElementById('session-list')?.classList.add('structured-list');
    document.getElementById('bridge-list')?.classList.add('structured-list', 'bridge-card-grid');
    document.getElementById('log-list')?.classList.add('structured-log-list');

    document.querySelectorAll('[data-scroll-target]').forEach(button => {
      if (button.dataset.layoutBound === '1') return;
      button.dataset.layoutBound = '1';
      button.addEventListener('click', () => {
        document.getElementById(button.dataset.scrollTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  }

  function moveDynamicDebugPanels() {
    const host = document.getElementById('live-debug-collector-host');
    const collector = document.getElementById('douyin-live-collector-panel');
    if (host && collector && collector.parentElement !== host) host.appendChild(collector);

    const debugButton = document.querySelector('.tabs [data-tab="simli-tuning"]');
    if (debugButton && debugButton.textContent !== '直播调试') debugButton.textContent = '直播调试';

    const validationResult = document.querySelector('#douyin-live-collector-panel [data-aliver-validation-results]');
    const placeholder = document.getElementById('live-debug-validation-placeholder');
    if (validationResult && placeholder && validationResult.parentElement !== placeholder) {
      placeholder.replaceChildren(validationResult.cloneNode(true));
    }
  }

  function bindLiveDebugActions() {
    const runButton = document.getElementById('live-debug-full-validation');
    if (runButton && runButton.dataset.layoutBound !== '1') {
      runButton.dataset.layoutBound = '1';
      runButton.addEventListener('click', () => {
        const target = document.getElementById('aliver-full-validation');
        if (!target) {
          toast('完整验证模块仍在加载，请稍等一秒后重试。', true);
          return;
        }
        target.click();
      });
    }

    const refreshButton = document.getElementById('live-debug-refresh-all');
    if (refreshButton && refreshButton.dataset.layoutBound !== '1') {
      refreshButton.dataset.layoutBound = '1';
      refreshButton.addEventListener('click', async () => {
        refreshButton.disabled = true;
        try {
          await Promise.all([
            typeof loadBridges === 'function' ? loadBridges() : Promise.resolve(),
            typeof loadSessions === 'function' ? loadSessions() : Promise.resolve(),
          ]);
          document.getElementById('avatar-debug-refresh')?.click();
          document.getElementById('douyin-collector-refresh')?.click();
          toast('直播调试状态已刷新');
        } catch (error) {
          toast(error.message, true);
        } finally {
          refreshButton.disabled = false;
        }
      });
    }
  }

  async function refreshVersionStatus() {
    const serverBadge = document.getElementById('live-debug-server-version');
    const bridgeBadge = document.getElementById('live-debug-bridge-version');
    const warning = document.getElementById('live-debug-version-warning');
    if (!serverBadge || !bridgeBadge || !warning) return;

    let serverVersion = VERSION;
    try {
      const health = typeof api === 'function' ? await api('/api/health') : null;
      serverVersion = String(health?.version || VERSION);
    } catch (_) {}

    const bridge = (state?.bridges || []).find(item => item.connected) || null;
    const bridgeVersion = String(bridge?.version || '未连接');
    serverBadge.textContent = `服务端 ${serverVersion}`;
    serverBadge.className = 'badge good';
    bridgeBadge.textContent = `Bridge ${bridgeVersion}`;
    bridgeBadge.className = `badge ${bridge && bridgeVersion === EXPECTED_BRIDGE_VERSION ? 'good' : 'warn'}`;

    if (!bridge) {
      warning.textContent = '没有在线 Windows Bridge。采集、VTube Studio、动作与口型验证暂时不能运行。';
      warning.className = 'diagnosis bad';
    } else if (bridgeVersion !== EXPECTED_BRIDGE_VERSION) {
      warning.textContent = `服务端已更新，但当前在线 Bridge 仍是 ${bridgeVersion}；必须停止旧 Bridge 并启动 ${EXPECTED_BRIDGE_VERSION}，否则仍会看到旧版 WGC 错误。`;
      warning.className = 'diagnosis bad';
    } else {
      warning.textContent = `服务端 ${serverVersion} 与 Bridge ${bridgeVersion} 已匹配，可以运行完整验证。`;
      warning.className = 'diagnosis good';
    }
  }

  function appendAsset(kind, id, url) {
    if (document.getElementById(id)) return;
    const element = document.createElement(kind === 'link' ? 'link' : 'script');
    element.id = id;
    if (kind === 'link') {
      element.rel = 'stylesheet';
      element.href = url;
      document.head.appendChild(element);
    } else {
      element.src = url;
      element.async = false;
      document.body.appendChild(element);
    }
  }

  function loadFeatureAssets() {
    const styles = [
      ['aliver-console-layout-v2-style', `/static/console_layout_v2.css?v=${VERSION}`],
      ['aliver-avatar-debug-style', `/static/avatar_debug_v2.css?v=${VERSION}`],
      ['aliver-avatar-action-runtime-style', `/static/avatar_action_runtime.css?v=${VERSION}`],
      ['aliver-director-plan-generator-style', `/static/director_plan_generator.css?v=${VERSION}`],
      ['aliver-douyin-live-collector-style', `/static/douyin_live_collector.css?v=${VERSION}`],
      ['aliver-douyin-capture-diagnostics-style', `/static/douyin_capture_diagnostics.css?v=${VERSION}`],
    ];
    styles.forEach(([id, url]) => appendAsset('link', id, url));

    const scripts = [
      ['aliver-local-time-patch', `/static/local_time_patch.js?v=${VERSION}`],
      ['aliver-diagnostics-zh', `/static/diagnostics_zh.js?v=${VERSION}`],
      ['aliver-auto-director-refresh-fix', `/static/auto_director_refresh_fix.js?v=${VERSION}`],
      ['aliver-avatar-debug-v2', `/static/avatar_debug_v2.js?v=${VERSION}`],
      ['aliver-management-v2', `/static/management_v2.js?v=${VERSION}`],
      ['aliver-audio-route-autostart', `/static/audio_route_autostart.js?v=${VERSION}`],
      ['aliver-vtube-motion-wizard', `/static/vtube_motion_wizard.js?v=${VERSION}`],
      ['aliver-vtube-motion-controls-fix', `/static/vtube_motion_controls_fix.js?v=${VERSION}`],
      ['aliver-avatar-action-runtime', `/static/avatar_action_runtime.js?v=${VERSION}`],
      ['aliver-director-plan-generator', `/static/director_plan_generator.js?v=${VERSION}`],
      ['aliver-douyin-live-collector', `/static/douyin_live_collector.js?v=${VERSION}`],
      ['aliver-douyin-capture-diagnostics', `/static/douyin_capture_diagnostics.js?v=${VERSION}`],
      ['aliver-full-validation', `/static/full_validation.js?v=${VERSION}`],
      ['aliver-provider-catalog-script', `/static/provider_catalog.js?v=${VERSION}`],
    ];
    scripts.forEach(([id, url]) => appendAsset('script', id, url));
  }

  function start() {
    ensureLiveDebugWorkspace();
    mergeDirectorWorkspace();
    optimizePageLayouts();
    bindLiveDebugActions();
    loadFeatureAssets();
    moveDynamicDebugPanels();
    refreshVersionStatus().catch(() => {});

    const observer = new MutationObserver(() => {
      moveDynamicDebugPanels();
      bindLiveDebugActions();
      optimizePageLayouts();
    });
    observer.observe(document.body, { childList: true, subtree: true });

    window.setInterval(() => {
      moveDynamicDebugPanels();
      bindLiveDebugActions();
      refreshVersionStatus().catch(() => {});
    }, 1500);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
