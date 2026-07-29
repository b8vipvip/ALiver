(() => {
  const ACTIONS = [
    ['thinking', '测试思考'],
    ['wave', '测试问候'],
    ['happy', '测试开心'],
    ['surprised', '测试惊讶'],
    ['reset', '恢复自然待机'],
  ];
  let renderTimer = null;
  let busy = false;

  function liveSnapshot() {
    const pre = document.getElementById('vtube-live-json');
    if (!pre) return null;
    try {
      return JSON.parse(pre.textContent || '{}');
    } catch (_) {
      return null;
    }
  }

  function ensureStyle() {
    if (document.getElementById('aliver-vtube-motion-wizard-style')) return;
    const style = document.createElement('style');
    style.id = 'aliver-vtube-motion-wizard-style';
    style.textContent = `
      .vtube-motion-wizard { margin-top: 16px; }
      .vtube-motion-wizard .wizard-controls {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 10px;
        margin: 12px 0;
      }
      .vtube-motion-wizard .wizard-controls label { margin: 0; }
      .vtube-motion-wizard .wizard-controls input,
      .vtube-motion-wizard .wizard-controls select { width: 100%; }
      .vtube-motion-wizard .wizard-metrics {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 10px;
        margin: 12px 0;
      }
      .vtube-motion-wizard .wizard-metrics article {
        border: 1px solid var(--border, #263241);
        border-radius: 10px;
        padding: 10px;
        background: rgba(255,255,255,.02);
      }
      .vtube-motion-wizard .wizard-metrics span,
      .vtube-motion-wizard .wizard-metrics small { display: block; color: var(--muted, #8d9bad); }
      .vtube-motion-wizard .wizard-metrics strong { display: block; margin: 4px 0; }
      .vtube-motion-wizard details pre { max-height: 260px; overflow: auto; }
    `;
    document.head.appendChild(style);
  }

  function ensurePanel() {
    let panel = document.getElementById('vtube-motion-wizard');
    if (panel) return panel;
    const host = document.getElementById('vtube-studio-debug-panel');
    const grid = host?.querySelector('.avatar-vtube-grid');
    if (!host || !grid) return null;

    panel = document.createElement('section');
    panel.id = 'vtube-motion-wizard';
    panel.className = 'vtube-motion-wizard';
    panel.innerHTML = `
      <div class="section-title">
        <div>
          <h3>VTube Studio 一键自然动作配置向导</h3>
          <p class="hint">直接通过 VTube Studio API 注入头部、位置、眼神、眉毛和微笑参数。无需手工创建热键；语音响起时自动从待机切换到说话动作。</p>
        </div>
        <div class="actions">
          <button id="vtube-motion-scan" type="button" class="secondary">重新扫描能力</button>
          <button id="vtube-motion-enable" type="button">一键扫描并启用</button>
          <button id="vtube-motion-disable" type="button" class="danger">停止自动动作</button>
        </div>
      </div>
      <div id="vtube-motion-status" class="diagnosis warn">等待活动 VTube Studio 会话。</div>
      <div class="wizard-controls">
        <label>动作风格
          <select id="vtube-motion-preset">
            <option value="gentle">自然柔和</option>
            <option value="lively">活泼明显</option>
          </select>
        </label>
        <label>待机动作强度
          <input id="vtube-motion-idle" type="number" min="0" max="2" step="0.05" value="0.55">
        </label>
        <label>说话动作强度
          <input id="vtube-motion-talking" type="number" min="0" max="2" step="0.05" value="0.85">
        </label>
        <label>语音检测阈值
          <input id="vtube-motion-threshold" type="number" min="0.005" max="0.95" step="0.01" value="0.08">
        </label>
        <label>动作刷新率
          <input id="vtube-motion-fps-input" type="number" min="5" max="30" step="1" value="15">
        </label>
      </div>
      <div class="actions" id="vtube-motion-tests">
        ${ACTIONS.map(([action, label]) => `<button type="button" class="secondary" data-vtube-procedural-test="${action}">${label}</button>`).join('')}
      </div>
      <div class="wizard-metrics">
        <article><span>自动动作</span><strong id="vtube-motion-running">未启用</strong><small id="vtube-motion-mode">模式：disabled</small></article>
        <article><span>语音检测</span><strong id="vtube-motion-speaking">未检测</strong><small id="vtube-motion-voice">VoiceVolume：0</small></article>
        <article><span>可控参数</span><strong id="vtube-motion-role-count">0</strong><small id="vtube-motion-roles">尚未扫描</small></article>
        <article><span>可用表情</span><strong id="vtube-motion-expression-count">0</strong><small id="vtube-motion-expression-map">尚未匹配</small></article>
      </div>
      <details>
        <summary>查看扫描结果与限制</summary>
        <pre id="vtube-motion-capability-json">尚未扫描。</pre>
      </details>
    `;
    grid.after(panel);

    panel.querySelector('#vtube-motion-scan').addEventListener('click', () => {
      scanMotion().catch(error => setStatus(error.message, 'bad'));
    });
    panel.querySelector('#vtube-motion-enable').addEventListener('click', () => {
      enableMotion().catch(error => setStatus(error.message, 'bad'));
    });
    panel.querySelector('#vtube-motion-disable').addEventListener('click', () => {
      disableMotion().catch(error => setStatus(error.message, 'bad'));
    });
    panel.querySelectorAll('[data-vtube-procedural-test]').forEach(button => {
      button.addEventListener('click', () => {
        testAction(button.dataset.vtubeProceduralTest).catch(error => setStatus(error.message, 'bad'));
      });
    });
    return panel;
  }

  function setStatus(message, kind = 'warn') {
    const box = document.getElementById('vtube-motion-status');
    if (!box) return;
    if (box.textContent !== message) box.textContent = message;
    const className = `diagnosis ${kind}`;
    if (box.className !== className) box.className = className;
  }

  function sessionContext() {
    const data = liveSnapshot();
    const runtime = data?.runtime;
    const provider = data?.provider;
    const session = data?.session;
    if (!runtime?.session_id || !session?.bridge_id || !provider?.id) {
      throw new Error('当前没有完整的 VTube Studio 活动会话');
    }
    return { data, runtime, provider, session };
  }

  function numericValue(id, fallback) {
    const number = Number(document.getElementById(id)?.value);
    return Number.isFinite(number) ? number : fallback;
  }

  function formConfig(recommended = {}, current = {}) {
    return {
      ...recommended,
      ...current,
      enabled: true,
      preset: document.getElementById('vtube-motion-preset')?.value || 'gentle',
      fps: numericValue('vtube-motion-fps-input', 15),
      auto_speech: true,
      voice_parameter: current.voice_parameter || recommended.voice_parameter || 'VoiceVolume',
      speech_threshold: numericValue('vtube-motion-threshold', 0.08),
      speech_hold_ms: current.speech_hold_ms || recommended.speech_hold_ms || 500,
      idle_intensity: numericValue('vtube-motion-idle', 0.55),
      talking_intensity: numericValue('vtube-motion-talking', 0.85),
      action_intensity: current.action_intensity || recommended.action_intensity || 1.0,
      expressions_enabled: true,
      expression_map: {
        ...(recommended.expression_map || {}),
        ...(current.expression_map || {}),
      },
    };
  }

  async function persistConfig(provider, motionEngine) {
    const settings = {
      ...(provider.settings || {}),
      motion_engine: motionEngine,
    };
    await api(`/api/providers/${provider.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ settings }),
    });
  }

  async function scanMotion({ silent = false } = {}) {
    if (busy) return null;
    busy = true;
    try {
      const { session } = sessionContext();
      if (!silent) setStatus('正在扫描当前模型的输入参数、Live2D 参数和表情资源……');
      const result = await sendBridgeCommand(
        session.bridge_id,
        'provider.vtube_studio.motion.scan',
        { session_id: session.id },
        30,
      );
      renderScan(result.capabilities || result.runtime?.motion_capabilities || {});
      if (!silent) setStatus('扫描完成。可直接点击“一键扫描并启用”。', 'good');
      return result;
    } finally {
      busy = false;
    }
  }

  async function enableMotion() {
    if (busy) return;
    const { runtime, provider, session } = sessionContext();
    setStatus('正在扫描并应用自然动作配置……');
    busy = true;
    try {
      const scan = await sendBridgeCommand(
        session.bridge_id,
        'provider.vtube_studio.motion.scan',
        { session_id: session.id },
        30,
      );
      const recommended = scan.recommended_config || scan.capabilities?.recommended_motion_engine || {};
      const current = runtime.motion?.config || runtime.config?.motion_engine || {};
      const config = formConfig(recommended, current);
      const updated = await sendBridgeCommand(
        session.bridge_id,
        'provider.vtube_studio.motion.configure',
        { session_id: session.id, motion_engine: config },
        30,
      );
      await persistConfig(provider, updated.motion?.config || config);
      renderScan(updated.motion_capabilities || scan.capabilities || {});
      setStatus('自然动作已启用并保存。GPT 开始说话时会自动切换到说话动作。', 'good');
      toast('VTube Studio 自然动作已一键启用');
      document.getElementById('avatar-debug-refresh')?.click();
    } finally {
      busy = false;
    }
  }

  async function disableMotion() {
    if (busy) return;
    const { runtime, provider, session } = sessionContext();
    setStatus('正在停止自动动作并恢复模型默认状态……');
    busy = true;
    try {
      const current = runtime.motion?.config || runtime.config?.motion_engine || {};
      const config = { ...current, enabled: false };
      const updated = await sendBridgeCommand(
        session.bridge_id,
        'provider.vtube_studio.motion.configure',
        { session_id: session.id, motion_engine: config },
        30,
      );
      await persistConfig(provider, updated.motion?.config || config);
      setStatus('自动动作已停止，模型已恢复默认状态。', 'good');
      document.getElementById('avatar-debug-refresh')?.click();
    } finally {
      busy = false;
    }
  }

  async function testAction(action) {
    const { session } = sessionContext();
    setStatus(`正在测试动作：${action}……`);
    const result = await sendBridgeCommand(
      session.bridge_id,
      'provider.vtube_studio.action',
      {
        session_id: session.id,
        action,
        force: true,
      },
      20,
    );
    const procedural = result.action_result?.procedural;
    const hotkey = result.action_result?.hotkey;
    const details = [
      procedural ? '程序化动作已执行' : '',
      hotkey ? `热键 ${hotkey.hotkey_name || hotkey.hotkey_id} 已叠加` : '',
    ].filter(Boolean).join('；');
    setStatus(`${action} 测试完成${details ? `：${details}` : ''}。`, 'good');
    document.getElementById('avatar-debug-refresh')?.click();
  }

  function renderScan(capabilities) {
    const counts = capabilities?.counts || {};
    const roleMap = capabilities?.role_map || {};
    const expressions = Array.isArray(capabilities?.expressions) ? capabilities.expressions : [];
    document.getElementById('vtube-motion-role-count').textContent =
      String(counts.resolved_motion_roles ?? Object.keys(roleMap).length);
    document.getElementById('vtube-motion-roles').textContent =
      Object.entries(roleMap).map(([role, name]) => `${role}→${name}`).join('、') || '未找到标准动作参数';
    document.getElementById('vtube-motion-expression-count').textContent =
      String(counts.expressions ?? expressions.length);
    const expressionMap = capabilities?.recommended_motion_engine?.expression_map || {};
    document.getElementById('vtube-motion-expression-map').textContent =
      Object.entries(expressionMap).filter(([, file]) => file).map(([action, file]) => `${action}→${file}`).join('、')
      || '未自动匹配表情，仍可使用参数动作';
    document.getElementById('vtube-motion-capability-json').textContent =
      JSON.stringify(capabilities || {}, null, 2);
  }

  function updateSemanticButtons(runtime) {
    const motion = runtime?.motion || {};
    const supported = new Set(motion.supported_actions || []);
    const hotkeys = runtime?.config?.hotkeys || {};
    document.querySelectorAll('[data-vtube-action]').forEach(button => {
      const action = button.dataset.vtubeAction;
      const available = Boolean(hotkeys[action]) || (Boolean(motion.enabled) && supported.has(action));
      button.disabled = !available;
      if (available && !hotkeys[action]) {
        button.title = '由自然动作引擎通过 VTube Studio 参数注入执行';
      }
    });
  }

  function renderRuntime() {
    const panel = ensurePanel();
    if (!panel) return false;
    const data = liveSnapshot();
    const runtime = data?.runtime;
    const motion = runtime?.motion || {};
    const capabilities = runtime?.motion_capabilities || {};
    const config = motion.config || runtime?.config?.motion_engine || {};

    if (config.preset) document.getElementById('vtube-motion-preset').value = config.preset;
    if (config.idle_intensity !== undefined) document.getElementById('vtube-motion-idle').value = config.idle_intensity;
    if (config.talking_intensity !== undefined) document.getElementById('vtube-motion-talking').value = config.talking_intensity;
    if (config.speech_threshold !== undefined) document.getElementById('vtube-motion-threshold').value = config.speech_threshold;
    if (config.fps !== undefined) document.getElementById('vtube-motion-fps-input').value = config.fps;

    document.getElementById('vtube-motion-running').textContent =
      motion.enabled ? (motion.running ? '运行中' : '已启用/未运行') : '未启用';
    document.getElementById('vtube-motion-mode').textContent =
      `模式：${motion.current_mode || 'disabled'} · 注入帧 ${motion.injected_frames || 0}`;
    document.getElementById('vtube-motion-speaking').textContent =
      motion.speaking ? '正在说话' : '静音/待机';
    document.getElementById('vtube-motion-voice').textContent =
      `${motion.voice_parameter || 'VoiceVolume'}：${Number(motion.voice_value || 0).toFixed(3)}`;

    if (Object.keys(capabilities).length) renderScan(capabilities);
    updateSemanticButtons(runtime);

    if (!runtime?.session_id) {
      setStatus('等待活动 VTube Studio 会话。');
    } else if (motion.last_error) {
      setStatus(`自然动作引擎异常：${motion.last_error}`, 'bad');
    } else if (motion.enabled && motion.running) {
      setStatus(`自然动作正在运行：当前 ${motion.current_mode || 'idle'} 模式。`, 'good');
    } else {
      setStatus('当前仅有口型驱动。点击“一键扫描并启用”即可加入待机、说话和语义动作。');
    }
    return true;
  }

  function scheduleRender() {
    if (renderTimer !== null) return;
    renderTimer = window.setTimeout(() => {
      renderTimer = null;
      renderRuntime();
    }, 120);
  }

  function start() {
    ensureStyle();
    if (!renderRuntime()) {
      setTimeout(start, 250);
      return;
    }
    const pre = document.getElementById('vtube-live-json');
    if (pre && pre.dataset.motionWizardObserved !== '1') {
      pre.dataset.motionWizardObserved = '1';
      new MutationObserver(scheduleRender).observe(pre, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
    setInterval(() => {
      const host = document.getElementById('tab-simli-tuning');
      if (host?.classList.contains('active')) scheduleRender();
    }, 1000);
  }

  start();
})();
