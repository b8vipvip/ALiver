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
  const panel = document.createElement('article');
  panel.id = 'simli-sync-panel';
  panel.className = 'panel';
  panel.innerHTML = `
    <div class="section-title">
      <div>
        <h2>Simli 音画同步 / LIVE_OUT</h2>
        <p class="hint">音频作为主时钟，视频按音频播放进度显示；优先自动输出到 CABLE-B Input。</p>
      </div>
      <button id="simli-sync-refresh" type="button" class="secondary">读取状态</button>
    </div>
    <div id="simli-sync-status" class="diagnosis warn">尚未读取 Simli 同步状态。</div>
    <pre id="simli-sync-json">启动 Simli 会话后读取状态。</pre>
  `;
  sessionsTab.appendChild(panel);

  function onlineBridgeId() {
    const selected = document.getElementById('session-bridge')?.value;
    if (selected) return selected;
    return (state.bridges || []).find(bridge => bridge.connected)?.id || '';
  }

  function renderSync(value) {
    const sessions = Object.values(value || {});
    const current = sessions.find(row => ['active', 'starting'].includes(row?.status)) || sessions[0];
    const sync = current?.av_sync;
    const status = document.getElementById('simli-sync-status');
    const output = document.getElementById('simli-sync-json');
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
    const health = sync.sync_health || sync.status;
    status.textContent = [
      `同步：${health}`,
      `偏差：${Number(sync.av_offset_ms || 0).toFixed(1)} ms`,
      `帧率：${Number(sync.render_fps || 0).toFixed(1)} FPS`,
      `LIVE_OUT：${sync.audio_output_device || '未播放'}`,
      sync.warning || '',
    ].filter(Boolean).join(' · ');
    status.className = `diagnosis ${health === 'good' ? 'good' : health === 'bad' ? 'bad' : 'warn'}`;
  }

  async function refresh() {
    const bridgeId = onlineBridgeId();
    if (!bridgeId) throw new Error('没有在线 Bridge');
    const data = await sendBridgeCommand(bridgeId, 'provider.simli.status', {}, 12);
    renderSync(data);
  }

  document.getElementById('simli-sync-refresh').addEventListener('click', () => {
    refresh().catch(error => toast(error.message, true));
  });

  setInterval(() => {
    if (sessionsTab.classList.contains('active')) refresh().catch(() => {});
  }, 2000);
})();

(() => {
  const script = document.createElement('script');
  script.src = '/static/diagnostics_zh.js?v=0.7.0';
  script.defer = true;
  document.head.appendChild(script);
})();
