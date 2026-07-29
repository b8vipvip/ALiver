(() => {
  const ACTIVE_STATUSES = new Set(['starting', 'active', 'running', 'ready', 'awaiting_manual']);
  const VTUBE_TYPES = new Set(['vtube_studio', 'local_vtube_studio']);
  const debugState = {
    session: null,
    provider: null,
    bridge: null,
    runtime: null,
    polling: false,
  };

  function start() {
    const panel = document.getElementById('tab-simli-tuning');
    const tabButton = document.querySelector('.tabs [data-tab="simli-tuning"]');
    if (!panel || !tabButton) {
      setTimeout(start, 100);
      return;
    }
    if (document.getElementById('avatar-active-session-context')) return;

    tabButton.textContent = '数字人调试';
    const heroTitle = panel.querySelector('.simli-tuning-hero h2');
    const heroHint = panel.querySelector('.simli-tuning-hero .hint');
    if (heroTitle) heroTitle.textContent = '数字人会话、模型与参数调试';
    if (heroHint) {
      heroHint.textContent = '打开本页会自动跟随最近启动的数字人会话，加载供应商配置、会话覆盖参数、Bridge 状态、当前模型与可用动作。';
    }

    const hero = panel.querySelector('.simli-tuning-hero');
    const context = document.createElement('article');
    context.id = 'avatar-active-session-context';
    context.className = 'panel avatar-debug-context';
    context.innerHTML = `
      <div class="section-title">
        <div>
          <h2>当前活动数字人会话</h2>
          <p class="hint">自动选择最近的 starting / active / running / ready 会话；无需手工复制 Session ID。</p>
        </div>
        <div class="actions">
          <span id="avatar-provider-badge" class="badge warn">未加载</span>
          <button id="avatar-debug-refresh" type="button" class="secondary">重新加载</button>
        </div>
      </div>
      <div id="avatar-session-summary" class="diagnosis warn">正在查找活动数字人会话……</div>
      <div id="avatar-session-cards" class="avatar-session-cards"></div>
      <details class="avatar-debug-details">
        <summary>查看供应商设置、会话覆盖和启动响应</summary>
        <pre id="avatar-session-json">尚未加载。</pre>
      </details>
    `;
    if (hero?.nextSibling) panel.insertBefore(context, hero.nextSibling);
    else panel.appendChild(context);

    const vtubePanel = document.createElement('article');
    vtubePanel.id = 'vtube-studio-debug-panel';
    vtubePanel.className = 'panel';
    vtubePanel.hidden = true;
    vtubePanel.innerHTML = `
      <div class="section-title">
        <div>
          <h2>VTube Studio 本地模型与动作</h2>
          <p class="hint">真实连接由所选 Windows Bridge 建立。首次连接请在 VTube Studio 弹窗中允许 ALiver 插件。</p>
        </div>
        <div class="actions">
          <button id="vtube-refresh" type="button" class="secondary">刷新模型</button>
          <button id="vtube-authorize" type="button" class="secondary">重新授权</button>
        </div>
      </div>
      <div id="vtube-status" class="diagnosis warn">等待活动 VTube Studio 会话。</div>
      <section class="tuning-metrics avatar-vtube-metrics">
        <article><span>当前模型</span><strong id="vtube-model-name">未加载</strong><small id="vtube-model-id">Model ID</small></article>
        <article><span>VTube Studio</span><strong id="vtube-version">未连接</strong><small id="vtube-api-url">API URL</small></article>
        <article><span>渲染帧率</span><strong id="vtube-fps">未测得</strong><small>来自 Statistics API</small></article>
        <article><span>插件连接</span><strong id="vtube-plugin-count">未测得</strong><small>授权状态与连接数</small></article>
      </section>
      <div class="grid two avatar-vtube-grid">
        <section>
          <h3>语义动作映射</h3>
          <p class="hint">按钮使用供应商设置中的 hotkeys 映射。未配置的动作会显示为不可用。</p>
          <div id="vtube-action-buttons" class="actions avatar-action-buttons"></div>
          <div id="vtube-action-result" class="diagnosis warn">尚未触发动作。</div>
        </section>
        <section>
          <h3>当前模型热键</h3>
          <div class="avatar-hotkey-trigger">
            <select id="vtube-hotkey-select"><option value="">当前模型没有可用热键</option></select>
            <button id="vtube-trigger-hotkey" type="button" class="secondary">触发所选热键</button>
          </div>
          <div id="vtube-hotkey-list" class="avatar-hotkey-list"><p class="hint">启动会话后自动读取。</p></div>
        </section>
      </div>
      <details class="avatar-debug-details" open>
        <summary>VTube Studio 实时状态与有效配置</summary>
        <pre id="vtube-live-json">尚未读取。</pre>
      </details>
    `;
    context.after(vtubePanel);

    const originalMetrics = panel.querySelector('.tuning-metrics:not(.avatar-vtube-metrics)');
    const originalGrid = panel.querySelector('.tuning-grid');
    const originalLivePanel = document.getElementById('tuning-live-json')?.closest('article');
    const originalSummary = document.getElementById('tuning-summary');

    function isVTubeSession() {
      return VTUBE_TYPES.has(debugState.session?.provider_type || '');
    }

    function applyProviderView() {
      const vtube = isVTubeSession();
      vtubePanel.hidden = !vtube;
      if (originalMetrics) originalMetrics.hidden = vtube;
      if (originalGrid) originalGrid.hidden = vtube;
      if (originalLivePanel) originalLivePanel.hidden = vtube;
      if (originalSummary) originalSummary.hidden = vtube;
    }

    function effectiveSettings(provider, session) {
      return {
        ...(provider?.settings || {}),
        ...(session?.request || {}),
      };
    }

    function renderSessionContext() {
      const session = debugState.session;
      const provider = debugState.provider;
      const bridge = debugState.bridge;
      const badge = document.getElementById('avatar-provider-badge');
      const summary = document.getElementById('avatar-session-summary');
      const cards = document.getElementById('avatar-session-cards');
      const output = document.getElementById('avatar-session-json');

      if (!session) {
        badge.textContent = '无活动会话';
        badge.className = 'badge warn';
        summary.textContent = '当前没有活动数字人会话。请先到“会话”页启动一个数字人会话。';
        summary.className = 'diagnosis warn';
        cards.innerHTML = '';
        output.textContent = JSON.stringify({ active_session: null }, null, 2);
        applyProviderView();
        return;
      }

      const effective = effectiveSettings(provider, session);
      badge.textContent = session.provider_type || '未知供应商';
      badge.className = 'badge good';
      summary.textContent = `已自动加载会话 ${session.id} · ${provider?.name || session.provider_name || '未知供应商'} · 状态 ${session.status} · Bridge ${bridge?.name || session.bridge_id || '未选择'}`;
      summary.className = 'diagnosis good';
      cards.innerHTML = `
        <article><span>供应商</span><strong>${escapeHtml(provider?.name || session.provider_name || '未知')}</strong><small>${escapeHtml(session.provider_type || '')}</small></article>
        <article><span>会话</span><strong>${escapeHtml(session.status)}</strong><small>${escapeHtml(session.id)}</small></article>
        <article><span>Bridge</span><strong>${escapeHtml(bridge?.name || '未连接')}</strong><small>${escapeHtml(bridge?.machine_name || session.bridge_id || '')}</small></article>
        <article><span>模型/资源</span><strong>${escapeHtml(effective.face_id || effective.avatar_id || effective.model_id || effective.model_name || '由运行端读取')}</strong><small>有效会话配置</small></article>
      `;
      output.textContent = JSON.stringify({
        active_session: session,
        provider: provider,
        effective_settings: effective,
        bridge: bridge,
      }, null, 2);
      applyProviderView();
    }

    function renderVTube(runtime) {
      debugState.runtime = runtime || null;
      const status = document.getElementById('vtube-status');
      const model = runtime?.model || {};
      const apiInfo = runtime?.api || {};
      const config = runtime?.config || effectiveSettings(debugState.provider, debugState.session);
      const hotkeys = Array.isArray(runtime?.hotkeys) ? runtime.hotkeys : [];

      document.getElementById('vtube-live-json').textContent = JSON.stringify({
        runtime,
        provider: debugState.provider,
        session: debugState.session,
        effective_settings: effectiveSettings(debugState.provider, debugState.session),
      }, null, 2);

      if (!runtime || runtime.status === 'missing') {
        status.textContent = 'Bridge 尚未报告该 VTube Studio 会话。请确认会话已启动并选择了在线 Bridge。';
        status.className = 'diagnosis warn';
      } else {
        const connected = Boolean(runtime.connected);
        const loaded = Boolean(model.loaded);
        status.textContent = `${connected ? 'API 已连接' : 'API 未连接'} · ${loaded ? `模型 ${model.name || model.id || '已加载'}` : '未加载模型'} · 会话 ${runtime.status || '未知'}${runtime.error ? ` · ${runtime.error}` : ''}`;
        status.className = `diagnosis ${connected && loaded && runtime.status === 'active' ? 'good' : runtime.error ? 'bad' : 'warn'}`;
      }

      document.getElementById('vtube-model-name').textContent = model.name || '未加载';
      document.getElementById('vtube-model-id').textContent = model.id || 'Model ID 未上报';
      document.getElementById('vtube-version').textContent = apiInfo.version || apiInfo.name || '未连接';
      document.getElementById('vtube-api-url').textContent = apiInfo.url || config.ws_url || 'ws://127.0.0.1:8001';
      document.getElementById('vtube-fps').textContent =
        apiInfo.framerate === null || apiInfo.framerate === undefined ? '未测得' : `${Number(apiInfo.framerate).toFixed(0)} FPS`;
      document.getElementById('vtube-plugin-count').textContent =
        runtime?.authenticated ? `已授权 · ${apiInfo.connected_plugins ?? '?'}` : '未授权';

      const actionMap = config.hotkeys || {};
      const labels = {
        idle: '待机',
        talking: '说话',
        thinking: '思考',
        wave: '挥手',
        happy: '开心',
        surprised: '惊讶',
        reset: '恢复',
      };
      document.getElementById('vtube-action-buttons').innerHTML = Object.entries(labels)
        .map(([action, label]) => {
          const mapped = actionMap[action] || '';
          return `<button type="button" class="secondary" data-vtube-action="${action}" ${mapped ? '' : 'disabled'} title="${escapeHtml(mapped || '未配置热键')}">${label}</button>`;
        })
        .join('');

      document.querySelectorAll('[data-vtube-action]').forEach(button => {
        button.addEventListener('click', () => {
          triggerAction(button.dataset.vtubeAction).catch(error => toast(error.message, true));
        });
      });

      const select = document.getElementById('vtube-hotkey-select');
      select.innerHTML = hotkeys.length
        ? '<option value="">选择当前模型热键</option>' + hotkeys.map(item =>
          `<option value="${escapeHtml(item.hotkeyID || item.name || '')}">${escapeHtml(item.name || item.hotkeyID || '未命名')} · ${escapeHtml(item.type || 'unknown')}</option>`
        ).join('')
        : '<option value="">当前模型没有可用热键</option>';
      document.getElementById('vtube-hotkey-list').innerHTML = hotkeys.length
        ? hotkeys.map(item => `
          <div class="avatar-hotkey-row">
            <strong>${escapeHtml(item.name || '未命名')}</strong>
            <span>${escapeHtml(item.type || 'unknown')}</span>
            <code>${escapeHtml(item.hotkeyID || '')}</code>
          </div>`).join('')
        : '<p class="hint">当前模型没有 API 可用热键。可以先只使用音频口型，稍后在 VTube Studio 中创建表情或动作热键。</p>';
    }

    function activeBridgeId() {
      return debugState.session?.bridge_id
        || document.getElementById('tuning-bridge')?.value
        || debugState.bridge?.id
        || '';
    }

    async function loadActiveSession() {
      if (debugState.polling) return;
      debugState.polling = true;
      try {
        const [sessions, providers, bridges] = await Promise.all([
          api('/api/sessions'),
          api('/api/providers'),
          api('/api/bridges'),
        ]);
        const session = sessions.find(row => ACTIVE_STATUSES.has(row.status)) || null;
        debugState.session = session;
        debugState.provider = session
          ? providers.find(row => row.id === session.provider_config_id) || null
          : null;
        debugState.bridge = session?.bridge_id
          ? bridges.find(row => row.id === session.bridge_id) || null
          : bridges.find(row => row.connected) || null;
        renderSessionContext();

        const bridgeSelect = document.getElementById('tuning-bridge');
        if (bridgeSelect && session?.bridge_id) bridgeSelect.value = session.bridge_id;

        if (isVTubeSession() && session?.id && activeBridgeId()) {
          const runtime = await sendBridgeCommand(
            activeBridgeId(),
            'provider.vtube_studio.status',
            { session_id: session.id },
            10,
          );
          renderVTube(runtime);
        }
      } finally {
        debugState.polling = false;
      }
    }

    async function refreshVTube() {
      if (!isVTubeSession() || !debugState.session) throw new Error('当前活动会话不是 VTube Studio');
      const runtime = await sendBridgeCommand(
        activeBridgeId(),
        'provider.vtube_studio.refresh',
        { session_id: debugState.session.id },
        15,
      );
      renderVTube(runtime);
      toast('已重新读取 VTube Studio 当前模型和热键');
    }

    async function authorizeVTube() {
      if (!isVTubeSession() || !debugState.session) throw new Error('当前活动会话不是 VTube Studio');
      toast('请查看 VTube Studio 窗口并允许 ALiver 插件访问');
      const runtime = await sendBridgeCommand(
        activeBridgeId(),
        'provider.vtube_studio.authorize',
        { session_id: debugState.session.id },
        30,
      );
      renderVTube(runtime);
      toast('VTube Studio 插件授权完成');
    }

    async function triggerAction(action = '', hotkey = '') {
      if (!isVTubeSession() || !debugState.session) throw new Error('当前活动会话不是 VTube Studio');
      const result = await sendBridgeCommand(
        activeBridgeId(),
        'provider.vtube_studio.action',
        {
          session_id: debugState.session.id,
          action: action || null,
          hotkey: hotkey || null,
          force: true,
        },
        15,
      );
      renderVTube(result);
      const box = document.getElementById('vtube-action-result');
      box.textContent = result.action_result
        ? `已触发：${result.action_result.hotkey_name || result.action_result.hotkey_id}`
        : `动作未触发：${result.reason || '未知原因'}`;
      box.className = `diagnosis ${result.action_result ? 'good' : 'warn'}`;
    }

    document.getElementById('avatar-debug-refresh').addEventListener('click', () => {
      loadActiveSession().catch(error => toast(error.message, true));
    });
    document.getElementById('vtube-refresh').addEventListener('click', () => {
      refreshVTube().catch(error => toast(error.message, true));
    });
    document.getElementById('vtube-authorize').addEventListener('click', () => {
      authorizeVTube().catch(error => toast(error.message, true));
    });
    document.getElementById('vtube-trigger-hotkey').addEventListener('click', () => {
      const hotkey = document.getElementById('vtube-hotkey-select').value;
      if (!hotkey) return toast('请先选择一个当前模型热键', true);
      triggerAction('', hotkey).catch(error => toast(error.message, true));
    });

    tabButton.addEventListener('click', () => {
      setTimeout(() => loadActiveSession().catch(error => toast(error.message, true)), 0);
    });

    installSessionHelper();
    setInterval(() => {
      if (panel.classList.contains('active')) {
        loadActiveSession().catch(() => {});
      }
    }, 2000);
  }

  function installSessionHelper() {
    const form = document.getElementById('session-form');
    const providerSelect = document.getElementById('session-provider');
    const bridgeSelect = document.getElementById('session-bridge');
    if (!form || !providerSelect || document.getElementById('vtube-session-helper')) return;

    const helper = document.createElement('div');
    helper.id = 'vtube-session-helper';
    helper.className = 'diagnosis warn';
    helper.hidden = true;
    helper.innerHTML = `
      <strong>VTube Studio 本地会话</strong>
      <span>会话启动时将由 Bridge 连接本机 8001 端口、请求插件授权，并读取当前已加载模型与热键。</span>
      <button id="vtube-session-template" type="button" class="secondary">填入本次覆盖模板</button>
    `;
    form.querySelector('button[type="submit"]')?.before(helper);

    function update() {
      const provider = (state.providers || []).find(row => row.id === providerSelect.value);
      const vtube = VTUBE_TYPES.has(provider?.provider_type || '');
      helper.hidden = !vtube;
      if (!vtube) return;
      const online = (state.bridges || []).filter(row => row.connected);
      if (bridgeSelect && !bridgeSelect.value && online.length === 1) {
        bridgeSelect.value = online[0].id;
      }
    }

    providerSelect.addEventListener('change', update);
    document.getElementById('vtube-session-template').addEventListener('click', () => {
      form.querySelector('[name="overrides"]').value = JSON.stringify({
        require_model_loaded: true,
        action_cooldown_ms: 1200,
      }, null, 2);
      toast('已填入 VTube Studio 会话覆盖模板');
    });
    setInterval(update, 1000);
    update();
  }

  start();
})();
