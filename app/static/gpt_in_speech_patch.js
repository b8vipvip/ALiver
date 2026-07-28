(() => {
  const original = document.getElementById('gpt-in-test');
  if (!original) return;

  const button = original.cloneNode(true);
  original.replaceWith(button);

  button.addEventListener('click', async () => {
    const initialText = button.textContent;
    button.disabled = true;
    button.textContent = '正在合成人声并发送…';
    try {
      const data = await sendBridgeCommand(
        selectedAudioBridge(),
        'audio.gpt_in.test',
        {
          text: '你好，ChatGPT。这是 ALiver 虚拟麦克风测试。听到这句话以后，请回答测试成功。 Hello ChatGPT, please reply test successful.',
        },
        60,
      );
      document.getElementById('gpt-in-test-json').textContent = JSON.stringify(data, null, 2);
      toast(
        `人声已送入 GPT_IN；等待 ChatGPT 回复。麦克风应选择：${data.microphone_hint || '匹配虚拟麦克风'}`,
      );
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = initialText;
    }
  });
})();

(() => {
  const form = document.getElementById('provider-form');
  const typeSelect = form?.querySelector('[name="provider_type"]');
  if (!form || !typeSelect || [...typeSelect.options].some(option => option.value === 'simli')) return;

  typeSelect.insertAdjacentHTML('beforeend', '<option value="simli">Simli（推荐）</option>');
  const helper = document.createElement('button');
  helper.type = 'button';
  helper.className = 'secondary';
  helper.textContent = '填入 Simli 推荐模板';
  helper.addEventListener('click', () => {
    typeSelect.value = 'simli';
    form.querySelector('[name="name"]').value = 'Simli Realtime';
    form.querySelector('[name="api_base_url"]').value = 'https://api.simli.ai';
    form.querySelector('[name="credentials"]').value = JSON.stringify({ api_key: '' }, null, 2);
    form.querySelector('[name="settings"]').value = JSON.stringify({
      face_id: '',
      transport: 'livekit',
      model: 'fasttalk',
      handle_silence: true,
      max_session_length: 3600,
      max_idle_time: 300,
      window_title: 'ALiver Simli Avatar',
      window_size: [720, 720],
      always_on_top: false,
      play_return_audio: true,
      auto_live_out: true,
      audio_output_device_name: '',
      sync_prebuffer_ms: 350,
      video_delay_ms: 0,
      late_video_drop_ms: 180,
    }, null, 2);
    toast('已填入 Simli 音画同步模板。安装 CABLE-B 后会自动作为 LIVE_OUT。');
  });
  form.querySelector('button[type="submit"]').before(helper);

  const bridgeSelect = document.getElementById('session-bridge');
  const bridgeLabel = bridgeSelect?.closest('label');
  if (bridgeLabel?.firstChild) bridgeLabel.firstChild.textContent = 'Bridge（Simli / LiveAvatar 必选）';
})();

(() => {
  const sessionsTab = document.getElementById('tab-sessions');
  if (!sessionsTab || document.getElementById('simli-sync-panel')) return;
  let currentSessionId = '';

  const panel = document.createElement('article');
  panel.id = 'simli-sync-panel';
  panel.className = 'panel';
  panel.innerHTML = `
    <div class="section-title">
      <div>
        <h2>Simli 客观音画诊断 / LIVE_OUT</h2>
        <p class="hint">不再依赖主观观察：记录真实播放事件，分析声音起点、口部运动、相关性偏差、PTS 帧率和视频速度。</p>
      </div>
      <div class="actions">
        <button id="simli-sync-refresh" type="button" class="secondary">读取状态</button>
        <button id="simli-sync-run" type="button">开始 12 秒自动检测</button>
      </div>
    </div>
    <div id="simli-sync-status" class="diagnosis warn">尚未读取 Simli 同步状态。</div>
    <p class="hint">检测期间让数字人连续说 8～12 秒。结果中正偏差表示口型晚于声音，负偏差表示口型早于声音。</p>
    <pre id="simli-sync-json">启动 Simli 会话后读取状态。</pre>
  `;
  sessionsTab.appendChild(panel);

  function onlineBridgeId() {
    const selected = document.getElementById('session-bridge')?.value;
    if (selected) return selected;
    return (state.bridges || []).find(bridge => bridge.connected)?.id || '';
  }

  function metric(value, digits = 1, suffix = '') {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '未测得';
    return `${Number(value).toFixed(digits)}${suffix}`;
  }

  function renderReport(report, sync = {}) {
    const status = document.getElementById('simli-sync-status');
    const health = sync.sync_health || (report.problems?.length ? 'bad' : 'measuring');
    const lines = [
      report.conclusion_zh || '正在收集客观数据。',
      `首次声音→口型：${metric(report.first_onset_offset_ms, 1, ' ms')}`,
      `持续相关性偏差：${metric(report.estimated_lip_sync_offset_ms, 1, ' ms')}`,
      `相关性置信度：${report.correlation_confidence || '未测得'}`,
      `视频速度：${metric(report.video_playback_speed_ratio, 3, '×')}`,
      `PTS/接收/渲染：${metric(report.source_pts_fps, 1)} / ${metric(report.receive_fps, 1)} / ${metric(report.render_fps_recent, 1)} FPS`,
      `时钟：${report.clock_mode || sync.video_clock_mode || '未知'}`,
      `LIVE_OUT：${sync.audio_output_device || '未播放'}`,
    ];
    status.textContent = lines.join(' · ');
    status.className = `diagnosis ${health === 'good' ? 'good' : health === 'bad' ? 'bad' : 'warn'}`;
  }

  function renderSync(value) {
    const sessions = Object.values(value || {});
    const current = sessions.find(row => ['active', 'starting'].includes(row?.status)) || sessions[0];
    const sync = current?.av_sync;
    const status = document.getElementById('simli-sync-status');
    const output = document.getElementById('simli-sync-json');
    currentSessionId = current?.session_id || '';
    output.textContent = JSON.stringify(value || {}, null, 2);
    if (!current) {
      status.textContent = '当前 Bridge 没有 Simli 会话。';
      status.className = 'diagnosis warn';
      return;
    }
    if (!sync) {
      status.textContent = `会话状态：${current.status}；等待同步器上报。`;
      status.className = 'diagnosis warn';
      return;
    }
    renderReport(sync.objective_diagnostics || {}, sync);
  }

  async function refresh() {
    const bridgeId = onlineBridgeId();
    if (!bridgeId) throw new Error('没有在线 Bridge');
    const data = await sendBridgeCommand(bridgeId, 'provider.simli.status', {}, 12);
    renderSync(data);
    return data;
  }

  async function runDiagnostic() {
    const bridgeId = onlineBridgeId();
    if (!bridgeId) throw new Error('没有在线 Bridge');
    if (!currentSessionId) await refresh();
    if (!currentSessionId) throw new Error('当前没有运行中的 Simli 会话');
    const button = document.getElementById('simli-sync-run');
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = '检测中 12 秒…';
    toast('检测已开始。现在让数字人连续说 8～12 秒。');
    try {
      const report = await sendBridgeCommand(
        bridgeId,
        'provider.simli.diagnostics.run',
        { session_id: currentSessionId, duration_seconds: 12 },
        25,
      );
      const statusData = await sendBridgeCommand(bridgeId, 'provider.simli.status', {}, 12);
      const current = Object.values(statusData || {}).find(row => row?.session_id === currentSessionId);
      renderReport(report, current?.av_sync || {});
      document.getElementById('simli-sync-json').textContent = JSON.stringify(report, null, 2);
      toast(report.conclusion_zh || '客观同步检测完成');
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  document.getElementById('simli-sync-refresh').addEventListener('click', () => {
    refresh().catch(error => toast(error.message, true));
  });
  document.getElementById('simli-sync-run').addEventListener('click', () => {
    runDiagnostic().catch(error => toast(error.message, true));
  });

  setInterval(() => {
    if (sessionsTab.classList.contains('active')) refresh().catch(() => {});
  }, 2000);
})();

(() => {
  const script = document.createElement('script');
  script.src = '/static/diagnostics_zh.js?v=0.7.1';
  script.defer = true;
  document.head.appendChild(script);
})();
