(() => {
  const state = {
    mounted: false,
    busy: false,
    bridges: [],
    devices: null,
    status: null,
    refreshTimer: null,
    configureTimer: null,
  };

  const presetNames = {
    original: '原声整理',
    natural_girl: '自然少女',
    sweet_young: '甜美小女孩感',
    energetic: '元气少女',
    gentle: '温柔少女',
    deep: '沉稳低声线',
    custom: '自定义',
  };

  const controls = [
    ['pitch_semitones', '音高 Pitch', -8, 8, 0.1, '半音；建议从 +1.5 到 +3.5 开始'],
    ['tone_age', '年轻/成熟音色塑形', -100, 100, 1, '负值更成熟厚实，正值更年轻明亮；第一版为频谱塑形'],
    ['low_cut_hz', '低切', 20, 220, 1, '去除低频轰鸣，女声通常 70–100 Hz'],
    ['bass_db', '低频厚度', -12, 12, 0.1, '负值更轻薄，正值更厚'],
    ['presence_db', '清晰与明亮度', -12, 12, 0.1, '提升 4 kHz 附近的存在感'],
    ['compressor_threshold_db', '压缩阈值', -48, 0, 0.5, '越低压缩越明显'],
    ['compressor_ratio', '压缩比', 1, 10, 0.1, '直播建议 1.8–3.0'],
    ['output_gain_db', '输出增益', -18, 12, 0.1, 'DSP 处理后的总增益'],
    ['limiter_threshold_db', '限制器上限', -12, 0, 0.1, '建议 -1 dB，防止爆音'],
  ];

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(value ?? ''));
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function style() {
    if (document.getElementById('aliver-realtime-dsp-style')) return;
    const node = document.createElement('style');
    node.id = 'aliver-realtime-dsp-style';
    node.textContent = `
      .dsp-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(330px,.85fr);gap:14px;align-items:start}
      .dsp-route-grid,.dsp-meter-grid,.dsp-control-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
      .dsp-control{border:1px solid var(--border,#263241);border-radius:12px;padding:12px;min-width:0;background:rgba(8,18,31,.25)}
      .dsp-control-head{display:flex;justify-content:space-between;gap:10px;margin-bottom:8px}.dsp-control output{color:#60a5fa;font-weight:700}
      .dsp-control input[type=range]{width:100%}.dsp-control small{display:block;color:var(--muted,#94a3b8);margin-top:6px;line-height:1.45}
      .dsp-status-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.dsp-status-card{border:1px solid var(--border,#263241);border-radius:10px;padding:11px;min-width:0}
      .dsp-status-card span,.dsp-status-card small{display:block;color:var(--muted,#94a3b8)}.dsp-status-card strong{display:block;margin:5px 0;overflow-wrap:anywhere}
      .dsp-meter{height:9px;border-radius:999px;background:#0b1420;overflow:hidden;border:1px solid var(--border,#263241);margin-top:8px}.dsp-meter i{display:block;height:100%;width:0;background:linear-gradient(90deg,#22c55e,#eab308,#ef4444);transition:width .18s ease}
      .dsp-route-guide{counter-reset:route;display:grid;gap:8px;margin-top:12px}.dsp-route-guide article{counter-increment:route;border-left:3px solid #3b82f6;padding:8px 10px;background:rgba(59,130,246,.08)}.dsp-route-guide article:before{content:counter(route);display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#2563eb;margin-right:8px;font-weight:700}
      .dsp-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:14px}.dsp-actions .spacer{flex:1}
      .dsp-ab-result pre{max-height:260px;overflow:auto}.dsp-live-note{border:1px solid #2f7c4b;background:rgba(34,197,94,.08);padding:12px;border-radius:10px;line-height:1.65}
      @media(max-width:1050px){.dsp-grid{grid-template-columns:1fr}.dsp-route-grid,.dsp-control-grid{grid-template-columns:1fr}}
    `;
    document.head.appendChild(node);
  }

  function controlMarkup([key, title, min, max, step, hint]) {
    return `<label class="dsp-control">
      <span class="dsp-control-head"><strong>${esc(title)}</strong><output id="dsp-${key}-value">0</output></span>
      <input id="dsp-${key}" type="range" min="${min}" max="${max}" step="${step}">
      <small>${esc(hint)}</small>
    </label>`;
  }

  function markup() {
    return `
      <header class="ops-page-heading">
        <div><span class="page-kicker">REAL-TIME VOICE DSP</span><h2>实时 DSP 语音处理器</h2>
        <p>直接处理 Chrome / ChatGPT 的实际音频波形。Voice 实时回答和消息菜单中的“朗读”都会经过同一条 DSP 链路。</p></div>
        <div class="actions"><button id="dsp-refresh" type="button" class="secondary">刷新设备与状态</button></div>
      </header>
      <section class="dsp-grid">
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">ROUTING & DSP</span><h2>音频路由与实时变声</h2></div><span id="dsp-badge" class="badge warn">未加载</span></div>
          <label>执行 Bridge<select id="dsp-bridge"><option value="">请选择在线 Bridge</option></select></label>
          <div id="dsp-dependency" class="diagnosis warn" style="margin-top:12px">正在读取 DSP 依赖。</div>
          <div class="dsp-route-grid" style="margin-top:12px">
            <label>原声音频输入（WASAPI Loopback）<select id="dsp-input"><option value="">扫描后选择</option></select></label>
            <label>处理后输出（独立虚拟声卡播放端）<select id="dsp-output"><option value="">扫描后选择</option></select></label>
            <label>声音预设<select id="dsp-preset">${Object.entries(presetNames).filter(([key]) => key !== 'custom').map(([key,name]) => `<option value="${key}">${name}</option>`).join('')}</select></label>
            <label>实时缓冲<select id="dsp-block-size"><option value="512">512 帧（低延迟/较高 CPU）</option><option value="1024">1024 帧（推荐）</option><option value="2048">2048 帧（更稳定/较高延迟）</option></select></label>
          </div>
          <div class="dsp-control-grid" style="margin-top:14px">${controls.map(controlMarkup).join('')}</div>
          <div class="dsp-actions">
            <button id="dsp-start" type="button">启动实时 DSP</button>
            <button id="dsp-apply" type="button" class="secondary">保存并实时应用</button>
            <button id="dsp-bypass" type="button" class="secondary">旁路原声</button>
            <button id="dsp-stop" type="button" class="danger">停止 DSP</button>
            <span class="spacer"></span><span id="dsp-save-state" class="hint"></span>
          </div>
          <div id="dsp-main-result" class="diagnosis warn" style="margin-top:12px">尚未启动。先扫描设备并确认使用三组互相隔离的虚拟声卡。</div>
        </article>
        <aside class="panel">
          <div class="section-title"><div><span class="page-kicker">MONITOR</span><h2>运行状态与 A/B 验证</h2></div></div>
          <div class="dsp-status-cards">
            <article class="dsp-status-card"><span>输入电平</span><strong id="dsp-input-db">-96 dBFS</strong><div class="dsp-meter"><i id="dsp-input-meter"></i></div></article>
            <article class="dsp-status-card"><span>输出电平</span><strong id="dsp-output-db">-96 dBFS</strong><div class="dsp-meter"><i id="dsp-output-meter"></i></div></article>
            <article class="dsp-status-card"><span>估算总延迟</span><strong id="dsp-latency">-- ms</strong><small>缓冲 + 当前块处理时间</small></article>
            <article class="dsp-status-card"><span>处理状态</span><strong id="dsp-blocks">0 块</strong><small id="dsp-xruns">0 次丢块/异常</small></article>
          </div>
          <div id="dsp-live-note" class="dsp-live-note" style="margin-top:12px">启动后，把 Chrome 输出设为原始 VB-CABLE；直播伴侣和 VTube Studio 则选择处理后虚拟声卡的录音端。</div>
          <div class="dsp-route-guide" id="dsp-route-guide"></div>
          <hr style="margin:16px 0;border-color:var(--border,#263241)">
          <h3>朗读 A/B 录音验证</h3>
          <p class="hint">点击后立即开始录制 10 秒。请马上到 ChatGPT 消息下方点“更多操作 → 朗读”。ALiver 会同时保存原声和处理后 WAV。</p>
          <div class="dsp-actions"><button id="dsp-record" type="button">录制 10 秒原声/处理后对比</button><button id="dsp-open-audio" type="button" class="secondary">转到音频路由</button></div>
          <div id="dsp-ab-result" class="dsp-ab-result diagnosis warn" style="margin-top:10px">尚未录制 A/B 对比。</div>
          <details style="margin-top:12px"><summary>查看 DSP 完整状态 JSON</summary><pre id="dsp-json">尚未加载。</pre></details>
        </aside>
      </section>`;
  }

  function selectedBridge() {
    const value = document.getElementById('dsp-bridge')?.value || '';
    if (!value) throw new Error('请先选择在线 Bridge');
    return value;
  }

  function payload() {
    const result = {
      preset: document.getElementById('dsp-preset').value || 'custom',
      input_device_key: document.getElementById('dsp-input').value,
      output_device_key: document.getElementById('dsp-output').value,
      block_size: Number(document.getElementById('dsp-block-size').value || 1024),
      sample_rate: 48000,
      channels: 2,
    };
    controls.forEach(([key]) => { result[key] = Number(document.getElementById(`dsp-${key}`).value); });
    return result;
  }

  function setControlValues(config = {}) {
    controls.forEach(([key, , min]) => {
      const input = document.getElementById(`dsp-${key}`);
      if (!input) return;
      const fallback = key === 'pitch_semitones' ? 3 : key === 'tone_age' ? 58 : Number(input.min || min);
      input.value = Number(config[key] ?? fallback);
      updateOutput(key);
    });
    if (config.block_size) document.getElementById('dsp-block-size').value = String(config.block_size);
    if (config.preset && presetNames[config.preset]) document.getElementById('dsp-preset').value = config.preset;
  }

  function unitFor(key) {
    if (key === 'pitch_semitones') return ' st';
    if (key === 'low_cut_hz') return ' Hz';
    if (key.endsWith('_db') || key.includes('threshold_db')) return ' dB';
    if (key === 'compressor_ratio') return ':1';
    return '';
  }

  function updateOutput(key) {
    const input = document.getElementById(`dsp-${key}`);
    const output = document.getElementById(`dsp-${key}-value`);
    if (input && output) output.textContent = `${input.value}${unitFor(key)}`;
  }

  function fillBridges() {
    const select = document.getElementById('dsp-bridge');
    const previous = select.value;
    const online = state.bridges.filter(item => item.status === 'online');
    select.innerHTML = '<option value="">请选择在线 Bridge</option>' + online.map(item =>
      `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.machine_name || '')} · ${esc(item.version || '')}</option>`
    ).join('');
    if (online.some(item => item.id === previous)) select.value = previous;
    else if (online.length === 1) select.value = online[0].id;
  }

  function deviceLabel(row) {
    return `${row.name} · ${row.default_sample_rate || '?'}Hz · ${row.virtual_family || row.kind}`;
  }

  function fillDevices(data) {
    state.devices = data;
    const input = document.getElementById('dsp-input');
    const output = document.getElementById('dsp-output');
    const currentInput = data.config?.input_device_key || data.recommendation?.input_loopback?.key || '';
    const currentOutput = data.config?.output_device_key || data.recommendation?.output_playback?.key || '';
    input.innerHTML = '<option value="">请选择原声 Loopback</option>' + (data.loopback_devices || []).map(row =>
      `<option value="${esc(row.key)}">${esc(deviceLabel(row))}</option>`
    ).join('');
    output.innerHTML = '<option value="">请选择处理后输出</option>' + (data.output_devices || []).filter(row => row.is_virtual).map(row =>
      `<option value="${esc(row.key)}">${esc(deviceLabel(row))}</option>`
    ).join('');
    input.value = currentInput;
    output.value = currentOutput;
    setControlValues(data.config || {});
    renderDependencies(data.dependencies, data.recommendation);
    renderRouteGuide(data.recommendation || {});
    renderStatus(data.status || {});
  }

  function renderDependencies(deps = {}, recommendation = {}) {
    const box = document.getElementById('dsp-dependency');
    if (deps.ready && recommendation.ready) {
      box.className = 'diagnosis good';
      box.textContent = '实时 DSP 依赖和三段音频路由均已就绪。';
    } else if (!deps.ready) {
      const missing = Object.entries(deps).filter(([key,value]) => key !== 'ready' && !value).map(([key]) => key).join(', ');
      box.className = 'diagnosis bad';
      box.textContent = `缺少实时 DSP 依赖：${missing}。请重新执行 setup_windows.ps1 或安装 requirements.txt。`;
    } else {
      box.className = 'diagnosis warn';
      box.textContent = (recommendation.warnings || []).join(' ') || '未找到独立的处理后输出虚拟声卡。';
    }
  }

  function renderRouteGuide(recommendation = {}) {
    const ins = recommendation.instructions || {};
    const root = document.getElementById('dsp-route-guide');
    root.innerHTML = [
      ['Chrome / ChatGPT 输出', ins.chrome_output || '标准 CABLE Input'],
      ['ALiver DSP 捕获', ins.dsp_input || '标准 CABLE Input 的 Loopback'],
      ['ALiver DSP 写入', ins.dsp_output || 'CABLE-B Input 等独立输出'],
      ['直播伴侣与 VTube Studio 麦克风', ins.douyin_microphone || 'CABLE-B Output 等处理后录音端'],
      ['ChatGPT Live 麦克风', ins.chatgpt_microphone || '继续使用 CABLE-A Output'],
    ].map(([title,value]) => `<article><strong>${esc(title)}</strong><div>${esc(value)}</div></article>`).join('');
  }

  function meterWidth(dbfs) {
    const value = Number(dbfs ?? -96);
    return `${Math.max(0, Math.min(100, (value + 60) / 60 * 100))}%`;
  }

  function renderStatus(data = {}) {
    state.status = data;
    const running = Boolean(data.running);
    const bypass = Boolean(data.config?.bypass);
    const badge = document.getElementById('dsp-badge');
    badge.textContent = running ? (bypass ? '运行中 · 旁路原声' : '实时处理运行中') : data.status === 'failed' ? '启动失败' : '已停止';
    badge.className = `badge ${running ? 'good' : data.status === 'failed' ? 'bad' : 'warn'}`;
    document.getElementById('dsp-input-db').textContent = `${Number(data.input_dbfs ?? -96).toFixed(1)} dBFS`;
    document.getElementById('dsp-output-db').textContent = `${Number(data.output_dbfs ?? -96).toFixed(1)} dBFS`;
    document.getElementById('dsp-input-meter').style.width = meterWidth(data.input_dbfs);
    document.getElementById('dsp-output-meter').style.width = meterWidth(data.output_dbfs);
    document.getElementById('dsp-latency').textContent = `${Number(data.estimated_latency_ms || 0).toFixed(1)} ms`;
    document.getElementById('dsp-blocks').textContent = `${Number(data.blocks_processed || 0).toLocaleString()} 块`;
    document.getElementById('dsp-xruns').textContent = `${Number(data.xruns || 0)} 次丢块/异常`;
    document.getElementById('dsp-bypass').textContent = bypass ? '恢复 DSP 效果' : '旁路原声';
    document.getElementById('dsp-json').textContent = JSON.stringify(data, null, 2);
    const result = document.getElementById('dsp-main-result');
    if (data.last_error) {
      result.className = 'diagnosis bad'; result.textContent = data.last_error;
    } else if (running) {
      result.className = 'diagnosis good';
      result.textContent = bypass
        ? 'DSP 音频流正在运行，但当前为原声旁路。'
        : 'DSP 正在实时处理。现在 ChatGPT Voice 和消息“朗读”都会经过变声链路。';
    }
  }

  async function refresh(showToast = false) {
    if (state.busy) return;
    state.busy = true;
    try {
      state.bridges = await api('/api/bridges');
      fillBridges();
      if (!document.getElementById('dsp-bridge').value) return;
      const data = await sendBridgeCommand(selectedBridge(), 'audio.dsp.devices', {}, 30);
      fillDevices(data);
      if (showToast && typeof toast === 'function') toast('实时 DSP 设备与状态已刷新');
    } catch (error) {
      const result = document.getElementById('dsp-main-result');
      result.className = 'diagnosis bad'; result.textContent = error.message;
      if (showToast && typeof toast === 'function') toast(error.message, true);
    } finally { state.busy = false; }
  }

  async function apply(showToast = true) {
    const data = await sendBridgeCommand(selectedBridge(), 'audio.dsp.configure', payload(), 30);
    renderStatus(data);
    document.getElementById('dsp-save-state').textContent = `已应用 ${new Date().toLocaleTimeString()}`;
    if (showToast && typeof toast === 'function') toast('DSP 参数已保存并应用');
    return data;
  }

  async function start() {
    const button = document.getElementById('dsp-start');
    const old = button.textContent;
    button.disabled = true; button.textContent = '正在启动音频流…';
    try {
      const data = await sendBridgeCommand(selectedBridge(), 'audio.dsp.start', payload(), 45);
      renderStatus(data);
      if (typeof toast === 'function') toast('实时 DSP 已启动');
    } finally { button.disabled = false; button.textContent = old; }
  }

  async function stop() {
    const data = await sendBridgeCommand(selectedBridge(), 'audio.dsp.stop', {}, 30);
    renderStatus(data);
    if (typeof toast === 'function') toast('实时 DSP 已停止');
  }

  async function toggleBypass() {
    const data = await sendBridgeCommand(selectedBridge(), 'audio.dsp.bypass', {
      bypass: !Boolean(state.status?.config?.bypass),
    }, 20);
    renderStatus(data);
  }

  async function recordCompare() {
    if (!state.status?.running) throw new Error('请先启动实时 DSP');
    const button = document.getElementById('dsp-record');
    const resultBox = document.getElementById('dsp-ab-result');
    const old = button.textContent;
    button.disabled = true; button.textContent = '录制中 10 秒…';
    resultBox.className = 'dsp-ab-result diagnosis warn';
    resultBox.textContent = '录制已经开始：请马上到 ChatGPT 消息下方点击“更多操作 → 朗读”。';
    try {
      const result = await sendBridgeCommand(selectedBridge(), 'audio.dsp.record_compare', { seconds: 10 }, 30);
      resultBox.className = 'dsp-ab-result diagnosis good';
      resultBox.innerHTML = `<strong>A/B 录制完成</strong><br>
        原声：${esc(result.original_path)}<br>处理后：${esc(result.processed_path)}<br>
        频谱重心：${esc(result.original?.spectral_centroid_hz)} Hz → ${esc(result.processed?.spectral_centroid_hz)} Hz<br>
        电平：${esc(result.original?.dbfs)} dBFS → ${esc(result.processed?.dbfs)} dBFS`;
      if (typeof toast === 'function') toast('原声与处理后 WAV 已保存');
    } finally { button.disabled = false; button.textContent = old; }
  }

  function scheduleLiveApply() {
    controls.forEach(([key]) => updateOutput(key));
    document.getElementById('dsp-preset').value = 'custom';
    clearTimeout(state.configureTimer);
    if (state.status?.running) {
      state.configureTimer = setTimeout(() => apply(false).catch(error => toast(error.message, true)), 280);
    }
  }

  function applyPresetLocally() {
    const preset = document.getElementById('dsp-preset').value;
    const values = state.devices?.presets?.[preset];
    if (values) setControlValues({ ...values, preset });
    if (state.status?.running) apply(false).catch(error => toast(error.message, true));
  }

  function bind() {
    document.getElementById('dsp-refresh').addEventListener('click', () => refresh(true));
    document.getElementById('dsp-bridge').addEventListener('change', () => refresh(false));
    document.getElementById('dsp-preset').addEventListener('change', applyPresetLocally);
    controls.forEach(([key]) => document.getElementById(`dsp-${key}`).addEventListener('input', scheduleLiveApply));
    document.getElementById('dsp-block-size').addEventListener('change', () => apply(false).catch(error => toast(error.message, true)));
    document.getElementById('dsp-input').addEventListener('change', () => apply(false).catch(error => toast(error.message, true)));
    document.getElementById('dsp-output').addEventListener('change', () => apply(false).catch(error => toast(error.message, true)));
    document.getElementById('dsp-start').addEventListener('click', () => start().catch(error => toast(error.message, true)));
    document.getElementById('dsp-apply').addEventListener('click', () => apply(true).catch(error => toast(error.message, true)));
    document.getElementById('dsp-stop').addEventListener('click', () => stop().catch(error => toast(error.message, true)));
    document.getElementById('dsp-bypass').addEventListener('click', () => toggleBypass().catch(error => toast(error.message, true)));
    document.getElementById('dsp-record').addEventListener('click', () => recordCompare().catch(error => {
      document.getElementById('dsp-ab-result').className = 'dsp-ab-result diagnosis bad';
      document.getElementById('dsp-ab-result').textContent = error.message;
      toast(error.message, true);
    }));
    document.getElementById('dsp-open-audio').addEventListener('click', () => {
      document.querySelector('.aliver-sidebar button[data-tab="audio"], nav.tabs button[data-tab="audio"]')?.click();
    });
  }

  async function poll() {
    if (!state.mounted || document.hidden) return;
    const bridge = document.getElementById('dsp-bridge')?.value;
    if (!bridge || state.busy) return;
    try { renderStatus(await sendBridgeCommand(bridge, 'audio.dsp.status', {}, 10)); } catch (_) {}
  }

  function mount() {
    const root = document.getElementById('voice-lab-root');
    if (!root || state.mounted || typeof sendBridgeCommand !== 'function') return false;
    state.mounted = true;
    style();
    root.innerHTML = markup();
    bind();
    refresh(false);
    state.refreshTimer = window.setInterval(poll, 1000);
    return true;
  }

  function startMount() {
    if (!mount()) window.setTimeout(startMount, 200);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startMount, { once: true });
  else startMount();
})();
