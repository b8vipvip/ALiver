(() => {
  let lastValidationPath = '';
  let running = false;

  function connectedBridgeId() {
    return document.getElementById('douyin-visible-bridge')?.value
      || (() => {
        try {
          return JSON.parse(document.getElementById('vtube-live-json')?.textContent || '{}')?.session?.bridge_id || '';
        } catch (_) {
          return '';
        }
      })()
      || (state.bridges || []).find(item => item.connected)?.id
      || '';
  }

  function liveSnapshot() {
    try {
      return JSON.parse(document.getElementById('vtube-live-json')?.textContent || '{}');
    } catch (_) {
      return {};
    }
  }

  function validationLabel(name) {
    return ({
      'collector.window_permissions': '直播伴侣窗口与权限',
      'collector.three_channels': '三级互动采集',
      'collector.wgc_preview': 'WGC 窗口帧',
      'collector.diagnostics': '采集诊断包',
      'avatar.connection_model': 'VTube Studio 连接与模型',
      'avatar.motion_capabilities': '动作参数能力',
      'avatar.actions': '动作连续校验',
      'avatar.mouth_audio_route': '口型与虚拟音频链路',
    })[name] || name || '未知步骤';
  }

  function escape(value) {
    return typeof escapeHtml === 'function'
      ? escapeHtml(String(value ?? ''))
      : String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  function renderValidation(result) {
    const targets = document.querySelectorAll('[data-aliver-validation-results]');
    const collector = result?.report?.collector_steps || [];
    const avatar = result?.report?.avatar?.steps || [];
    const rows = [...collector, ...avatar];
    const summary = result?.summary || {};
    const html = `
      <div class="diagnosis ${summary.failed ? 'warn' : 'good'}">
        验证完成：${Number(summary.passed || 0)} 项通过，${Number(summary.failed || 0)} 项失败，结果 ${escape(summary.overall || 'unknown')}。
      </div>
      <div class="aliver-validation-grid">
        ${rows.map(item => `
          <article class="${item.ok ? 'validation-pass' : 'validation-fail'}">
            <span>${escape(validationLabel(item.name))}</span>
            <strong>${item.ok ? '通过' : item.status === 'skipped' ? '跳过' : '失败'}</strong>
            <small>${item.error ? escape(item.error) : `${Number(item.elapsed_ms || 0)} ms`}</small>
          </article>
        `).join('') || '<p class="hint">没有返回验证步骤。</p>'}
      </div>
      <details>
        <summary>完整验证 JSON</summary>
        <pre>${escape(JSON.stringify(result, null, 2))}</pre>
      </details>
      <div class="aliver-validation-path-row">
        <code>${escape(result?.path || '未生成验证包')}</code>
        <button type="button" class="secondary" data-open-validation-folder ${result?.path ? '' : 'disabled'}>打开文件夹</button>
      </div>
    `;
    targets.forEach(target => { target.innerHTML = html; });
    document.querySelectorAll('[data-open-validation-folder]').forEach(button => {
      button.addEventListener('click', () => openValidationFolder().catch(error => toast(error.message, true)));
    });
  }

  async function runValidation(options = {}) {
    if (running) throw new Error('完整验证正在运行，请等待当前任务结束');
    const bridgeId = connectedBridgeId();
    if (!bridgeId) throw new Error('没有在线 Windows Bridge');
    running = true;
    document.querySelectorAll('[data-run-aliver-validation]').forEach(button => {
      button.disabled = true;
      button.dataset.originalText = button.textContent;
      button.textContent = '正在自动验证…';
    });
    document.querySelectorAll('[data-aliver-validation-results]').forEach(target => {
      target.innerHTML = '<div class="diagnosis warn">正在连续验证采集通道、窗口捕获、权限、数字人动作和口型链路。任一步失败都不会中断后续步骤。</div>';
    });
    try {
      const snapshot = liveSnapshot();
      const sessionId = options.session_id || snapshot?.session?.id || '';
      const result = await sendBridgeCommand(
        bridgeId,
        'aliver.full_validation',
        {
          session_id: sessionId || null,
          test_actions: options.test_actions !== false,
          test_mouth: options.test_mouth !== false,
          skip_collector: Boolean(options.skip_collector),
          skip_avatar: Boolean(options.skip_avatar),
        },
        240,
      );
      lastValidationPath = String(result.path || '');
      renderValidation(result);
      toast(`完整验证结束：${Number(result.summary?.passed || 0)} 项通过，${Number(result.summary?.failed || 0)} 项失败`);
      document.getElementById('avatar-debug-refresh')?.click();
      document.getElementById('douyin-collector-refresh')?.click();
      return result;
    } finally {
      running = false;
      document.querySelectorAll('[data-run-aliver-validation]').forEach(button => {
        button.disabled = false;
        button.textContent = button.dataset.originalText || '一键完整验证并导出';
      });
    }
  }

  async function openValidationFolder() {
    const bridgeId = connectedBridgeId();
    if (!bridgeId) throw new Error('没有在线 Windows Bridge');
    if (!lastValidationPath) throw new Error('请先运行一次完整验证');
    await sendBridgeCommand(
      bridgeId,
      'douyin.visible.open_diagnostics_folder',
      { path: lastValidationPath },
      30,
    );
  }

  function ensureCollectorValidation() {
    const panel = document.getElementById('douyin-live-collector-panel');
    if (!panel) return false;
    const actions = panel.querySelector('.actions');
    if (!actions || document.getElementById('aliver-full-validation')) return true;
    const button = document.createElement('button');
    button.id = 'aliver-full-validation';
    button.type = 'button';
    button.className = 'secondary';
    button.dataset.runAliverValidation = 'all';
    button.textContent = '一键完整验证并导出';
    actions.appendChild(button);
    button.addEventListener('click', () => runValidation().catch(error => toast(error.message, true)));

    const results = document.createElement('details');
    results.open = true;
    results.className = 'aliver-validation-results-panel';
    results.innerHTML = `
      <summary>自动验证结果</summary>
      <div data-aliver-validation-results><p class="hint">点击“一键完整验证并导出”，系统会自动跑完全部项目并生成 ZIP。</p></div>
    `;
    const diagnostics = document.getElementById('douyin-capture-diagnostics');
    if (diagnostics) diagnostics.before(results);
    else panel.appendChild(results);
    return true;
  }

  function numberValue(id, fallback) {
    const value = Number(document.getElementById(id)?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function applyRuntimeToForm() {
    const data = liveSnapshot();
    const runtime = data?.runtime || {};
    const provider = data?.provider || {};
    const motion = runtime?.motion?.config || runtime?.config?.motion_engine || provider?.settings?.motion_engine || {};
    const settings = { ...(provider.settings || {}), ...(runtime.config || {}) };
    const values = {
      'avatar-param-preset': motion.preset || 'gentle',
      'avatar-param-idle': motion.idle_intensity ?? 0.55,
      'avatar-param-talking': motion.talking_intensity ?? 0.85,
      'avatar-param-action': motion.action_intensity ?? 1.0,
      'avatar-param-threshold': motion.speech_threshold ?? 0.08,
      'avatar-param-hold': motion.speech_hold_ms ?? 500,
      'avatar-param-fps': motion.fps ?? 15,
      'avatar-param-voice': motion.voice_parameter || settings.mouth_input_parameter || 'VoiceVolume',
      'avatar-param-mouth': settings.mouth_output_parameter || 'ParamMouthOpenY',
    };
    Object.entries(values).forEach(([id, value]) => {
      const element = document.getElementById(id);
      if (element && document.activeElement !== element) element.value = value;
    });
    const status = document.getElementById('avatar-parameter-live-status');
    if (status) {
      status.textContent = runtime?.session_id
        ? `会话 ${runtime.session_id} · 自动动作 ${runtime.motion?.running ? '运行中' : '未运行'} · 当前模式 ${runtime.motion?.current_mode || 'disabled'} · Voice ${Number(runtime.motion?.voice_value || 0).toFixed(3)}`
        : '等待活动 VTube Studio 会话。';
      status.className = `diagnosis ${runtime?.session_id ? 'good' : 'warn'}`;
    }
  }

  function avatarContext() {
    const data = liveSnapshot();
    if (!data?.session?.id || !data?.session?.bridge_id || !data?.provider?.id) {
      throw new Error('当前没有完整的 VTube Studio 活动会话');
    }
    return data;
  }

  async function scanAvatarParameters() {
    const data = avatarContext();
    const result = await sendBridgeCommand(
      data.session.bridge_id,
      'provider.vtube_studio.motion.scan',
      { session_id: data.session.id },
      45,
    );
    document.getElementById('avatar-parameter-json').textContent = JSON.stringify(result, null, 2);
    toast('已扫描动作参数、Live2D 参数与表情资源');
    document.getElementById('avatar-debug-refresh')?.click();
  }

  async function saveAvatarParameters() {
    const data = avatarContext();
    const current = data?.runtime?.motion?.config || data?.runtime?.config?.motion_engine || {};
    const motionEngine = {
      ...current,
      enabled: true,
      preset: document.getElementById('avatar-param-preset')?.value || 'gentle',
      idle_intensity: numberValue('avatar-param-idle', 0.55),
      talking_intensity: numberValue('avatar-param-talking', 0.85),
      action_intensity: numberValue('avatar-param-action', 1.0),
      speech_threshold: numberValue('avatar-param-threshold', 0.08),
      speech_hold_ms: numberValue('avatar-param-hold', 500),
      fps: numberValue('avatar-param-fps', 15),
      auto_speech: true,
      voice_parameter: document.getElementById('avatar-param-voice')?.value || 'VoiceVolume',
      expressions_enabled: true,
    };
    const runtime = await sendBridgeCommand(
      data.session.bridge_id,
      'provider.vtube_studio.motion.configure',
      { session_id: data.session.id, motion_engine: motionEngine },
      45,
    );
    const settings = {
      ...(data.provider.settings || {}),
      mouth_input_parameter: motionEngine.voice_parameter,
      mouth_output_parameter: document.getElementById('avatar-param-mouth')?.value || 'ParamMouthOpenY',
      motion_engine: runtime.motion?.config || motionEngine,
    };
    await api(`/api/providers/${data.provider.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ settings }),
    });
    document.getElementById('avatar-parameter-json').textContent = JSON.stringify(runtime, null, 2);
    toast('数字人动作与口型参数已保存并启用');
    document.getElementById('avatar-debug-refresh')?.click();
  }

  function ensureAvatarParameterPanel() {
    const host = document.getElementById('vtube-studio-debug-panel');
    if (!host) return false;
    if (document.getElementById('avatar-parameter-debug-panel')) {
      applyRuntimeToForm();
      return true;
    }
    const panel = document.createElement('section');
    panel.id = 'avatar-parameter-debug-panel';
    panel.className = 'avatar-parameter-debug-panel';
    panel.innerHTML = `
      <div class="section-title">
        <div>
          <h3>数字人动作、口型与参数调试</h3>
          <p class="hint">参数不会再隐藏。可扫描模型能力、调整待机/说话/语义动作强度，并自动向虚拟音频线播放测试信号后读取 VoiceVolume 与 ParamMouthOpenY。</p>
        </div>
      </div>
      <div id="avatar-parameter-live-status" class="diagnosis warn">等待活动 VTube Studio 会话。</div>
      <div class="avatar-parameter-grid">
        <label>动作风格<select id="avatar-param-preset"><option value="gentle">自然柔和</option><option value="lively">活泼明显</option></select></label>
        <label>待机强度<input id="avatar-param-idle" type="number" min="0" max="2" step="0.05" value="0.55"></label>
        <label>说话强度<input id="avatar-param-talking" type="number" min="0" max="2" step="0.05" value="0.85"></label>
        <label>语义动作强度<input id="avatar-param-action" type="number" min="0" max="2" step="0.05" value="1"></label>
        <label>语音阈值<input id="avatar-param-threshold" type="number" min="0.005" max="0.95" step="0.01" value="0.08"></label>
        <label>说话保持毫秒<input id="avatar-param-hold" type="number" min="100" max="5000" step="50" value="500"></label>
        <label>动作刷新率<input id="avatar-param-fps" type="number" min="5" max="30" step="1" value="15"></label>
        <label>语音输入参数<input id="avatar-param-voice" value="VoiceVolume"></label>
        <label>口型输出参数<input id="avatar-param-mouth" value="ParamMouthOpenY"></label>
      </div>
      <div class="actions">
        <button id="avatar-param-scan" type="button" class="secondary">扫描动作参数</button>
        <button id="avatar-param-save" type="button">保存参数并启用</button>
        <button type="button" class="secondary" data-run-aliver-validation="actions">一键动作校验</button>
        <button type="button" class="secondary" data-run-aliver-validation="mouth">一键口型校验</button>
        <button type="button" class="secondary" data-run-aliver-validation="avatar">数字人完整校验并导出</button>
      </div>
      <div data-aliver-validation-results><p class="hint">动作校验会依次触发思考、开心、惊讶、问候和恢复；口型校验会自动播放测试信号并采样参数。</p></div>
      <details><summary>动作与口型原始结果</summary><pre id="avatar-parameter-json">尚未运行。</pre></details>
    `;
    const liveDetails = host.querySelector('.avatar-debug-details');
    if (liveDetails) liveDetails.before(panel);
    else host.appendChild(panel);

    document.getElementById('avatar-param-scan').addEventListener('click', () => scanAvatarParameters().catch(error => toast(error.message, true)));
    document.getElementById('avatar-param-save').addEventListener('click', () => saveAvatarParameters().catch(error => toast(error.message, true)));
    panel.querySelector('[data-run-aliver-validation="actions"]').addEventListener('click', () => runValidation({ skip_collector: true, test_actions: true, test_mouth: false }).catch(error => toast(error.message, true)));
    panel.querySelector('[data-run-aliver-validation="mouth"]').addEventListener('click', () => runValidation({ skip_collector: true, test_actions: false, test_mouth: true }).catch(error => toast(error.message, true)));
    panel.querySelector('[data-run-aliver-validation="avatar"]').addEventListener('click', () => runValidation({ skip_collector: true }).catch(error => toast(error.message, true)));
    applyRuntimeToForm();
    return true;
  }

  function ensureStyle() {
    if (document.getElementById('aliver-full-validation-style')) return;
    const style = document.createElement('style');
    style.id = 'aliver-full-validation-style';
    style.textContent = `
      .aliver-validation-grid, .avatar-parameter-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:12px 0; }
      .aliver-validation-grid article { border:1px solid var(--border); border-radius:10px; padding:10px; background:rgba(255,255,255,.02); }
      .aliver-validation-grid span, .aliver-validation-grid small { display:block; color:var(--muted); }
      .aliver-validation-grid strong { display:block; margin:4px 0; }
      .validation-pass { border-color:rgba(42,190,120,.5)!important; }
      .validation-fail { border-color:rgba(255,80,100,.55)!important; }
      .aliver-validation-results-panel, .avatar-parameter-debug-panel { margin-top:16px; }
      .aliver-validation-path-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }
      .aliver-validation-path-row code { flex:1; min-width:240px; overflow-wrap:anywhere; }
      .avatar-parameter-grid label { margin:0; }
      .avatar-parameter-grid input, .avatar-parameter-grid select { width:100%; }
      [data-aliver-validation-results] pre { max-height:360px; overflow:auto; }
    `;
    document.head.appendChild(style);
  }

  function start() {
    ensureStyle();
    ensureCollectorValidation();
    ensureAvatarParameterPanel();
    setInterval(() => {
      ensureCollectorValidation();
      ensureAvatarParameterPanel();
      applyRuntimeToForm();
    }, 1000);
  }

  start();
})();
