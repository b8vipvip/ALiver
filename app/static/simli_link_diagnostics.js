(() => {
  if (document.getElementById('tab-simli-link')) return;

  const TEST_TEXT = `请用正常、自然、连续的语速说大约十五秒：大家好，欢迎来到直播间。现在正在进行数字人实时链路测试，请保持正常语速，不要唱歌，也不要刻意放慢。今天我们会聊一些轻松有趣的话题，欢迎大家在评论区一起互动。测试马上结束，谢谢大家。`;
  const linkState = {
    bridgeId: '',
    sessionId: '',
    extensionId: '',
    polling: false,
    testRunning: false,
  };

  const css = document.createElement('link');
  css.rel = 'stylesheet';
  css.href = '/static/simli_link_diagnostics.css?v=0.9.0';
  document.head.appendChild(css);

  const tabButton = document.createElement('button');
  tabButton.type = 'button';
  tabButton.dataset.tab = 'simli-link';
  tabButton.textContent = '链路诊断';
  const tabs = document.querySelector('.tabs');
  const logsButton = tabs?.querySelector('[data-tab="logs"]');
  if (logsButton) tabs.insertBefore(tabButton, logsButton);
  else tabs?.appendChild(tabButton);

  const panel = document.createElement('section');
  panel.id = 'tab-simli-link';
  panel.className = 'tab-panel';
  panel.innerHTML = `
    <article class="panel link-diagnostics-hero">
      <div class="section-title">
        <div>
          <h2>Simli 实时链路诊断</h2>
          <p class="hint">每 2 秒客观采样 WebRTC 网络、LiveKit 解码、ALiver 帧消费、窗口渲染、音频缓冲和关键时间线，自动判断瓶颈位于哪里。</p>
        </div>
        <span id="link-health-badge" class="badge warn">等待采样</span>
      </div>
      <div class="link-selectors">
        <div class="inline-fields">
          <label>执行 Bridge
            <select id="link-bridge"><option value="">请选择在线 Bridge</option></select>
          </label>
          <label>标准测试使用的导演扩展
            <select id="link-extension"><option value="">可选：选择在线扩展</option></select>
          </label>
        </div>
        <div class="actions">
          <button id="link-refresh" type="button" class="secondary">立即读取</button>
          <button id="link-test" type="button">运行 20 秒标准链路测试</button>
          <button id="link-report" type="button" class="secondary">生成链路报告</button>
          <button id="link-bundle" type="button" class="secondary">生成故障包</button>
        </div>
      </div>
      <div id="link-conclusion" class="diagnosis warn">启动 Simli 会话后，系统会自动开始采样。</div>
    </article>

    <section class="link-health-grid">
      <article><span>网络 RTT</span><strong id="link-rtt">—</strong><small id="link-route">WebRTC 路径：—</small></article>
      <article><span>网络抖动 / 丢包</span><strong id="link-jitter-loss">—</strong><small>越低越好；持续丢包会造成卡顿</small></article>
      <article><span>LiveKit 视频</span><strong id="link-livekit-fps">—</strong><small id="link-livekit-bitrate">解码 FPS / 接收码率</small></article>
      <article><span>ALiver 帧链</span><strong id="link-aliver-fps">—</strong><small>接收 FPS → 实际窗口渲染 FPS</small></article>
      <article><span>视频队列</span><strong id="link-video-queue">—</strong><small id="link-video-drops">本地积压 / 丢帧</small></article>
      <article><span>返回音频缓冲</span><strong id="link-audio-buffer">—</strong><small id="link-waveout">Simli 队列 / Windows 播放队列</small></article>
      <article><span>GPT_OUT 输入</span><strong id="link-gpt-out">—</strong><small id="link-input-queue">送往 Simli 的本地队列</small></article>
      <article><span>A/V 调度</span><strong id="link-av-offset">—</strong><small id="link-lateness">当前偏差 / 调度迟到</small></article>
    </section>

    <div class="grid two">
      <article class="panel">
        <h2>自动瓶颈判断</h2>
        <div id="link-bottleneck" class="diagnosis warn">尚无足够数据。</div>
        <h3>证据</h3>
        <ul id="link-evidence" class="link-diagnosis-list"><li>等待实时采样。</li></ul>
        <h3>建议</h3>
        <ul id="link-suggestions" class="link-diagnosis-list"><li>等待实时采样。</li></ul>
      </article>
      <article class="panel">
        <h2>端到端关键时间线</h2>
        <div class="link-stage-grid">
          <article><span>GPT_OUT 首次有效语音</span><strong id="link-t-input">—</strong><small>ALiver 捕获到 ChatGPT 声音</small></article>
          <article><span>首次送入 Simli</span><strong id="link-t-send">—</strong><small id="link-t-send-delta">输入→发送</small></article>
          <article><span>Simli 返回语音</span><strong id="link-t-return">—</strong><small id="link-t-return-delta">输入→返回语音</small></article>
          <article><span>首帧 / 首次口型</span><strong id="link-t-mouth">—</strong><small id="link-t-mouth-delta">返回声音→口型</small></article>
        </div>
      </article>
    </div>

    <article class="panel">
      <div class="section-title">
        <div><h2>三级视频吞吐</h2><p class="hint">这是判断“国外网络慢”还是“本机处理慢”的核心：先看 LiveKit 解码，再看 ALiver 收帧，最后看窗口渲染。</p></div>
      </div>
      <div class="link-flow">
        <div class="flow-node"><span>① LiveKit 解码</span><strong id="flow-decode">— FPS</strong><small>网络/Simli 到本机 WebRTC 层</small></div>
        <div class="flow-node"><span>② ALiver 取帧</span><strong id="flow-receive">— FPS</strong><small>SDK → Python 消费速度</small></div>
        <div class="flow-node"><span>③ OpenCV 显示</span><strong id="flow-render">— FPS</strong><small>Python → 数字人窗口</small></div>
      </div>
    </article>

    <article class="panel">
      <div class="section-title"><h2>最近实时样本</h2><span id="link-sample-count" class="hint">0 个样本</span></div>
      <div class="link-history"><table><thead><tr>
        <th>时间</th><th>RTT</th><th>Jitter</th><th>Loss</th><th>Decode FPS</th><th>Receive FPS</th><th>Render FPS</th><th>Video Queue</th><th>Audio Buffer</th><th>判断</th>
      </tr></thead><tbody id="link-history-body"><tr><td colspan="10">等待采样。</td></tr></tbody></table></div>
    </article>

    <article class="panel link-paths">
      <div class="section-title"><h2>自动日志与原始数据</h2><span class="hint">这些文件会自动进入 ALiver 故障包，无需人工挑日志。</span></div>
      <div>逐样本 JSONL：<code id="link-event-path">—</code></div>
      <div>聚合报告：<code id="link-report-path">—</code></div>
      <details><summary>最新完整 JSON</summary><pre id="link-live-json">尚未读取。</pre></details>
    </article>
  `;
  document.querySelector('main')?.appendChild(panel);

  const byId = id => document.getElementById(id);
  const metric = (value, digits = 1, suffix = '') => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    return `${Number(value).toFixed(digits)}${suffix}`;
  };
  const shortTime = value => value ? new Date(value).toLocaleTimeString() : '—';
  const maxMetric = (...values) => {
    const rows = values.filter(value => value !== null && value !== undefined && !Number.isNaN(Number(value))).map(Number);
    return rows.length ? Math.max(...rows) : null;
  };

  function activate() {
    document.querySelectorAll('.tabs button').forEach(button => button.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(item => item.classList.remove('active'));
    tabButton.classList.add('active');
    panel.classList.add('active');
    refreshEverything().catch(error => toast(error.message, true));
  }
  tabButton.addEventListener('click', activate);

  async function loadSelectors() {
    const [bridges, extensions] = await Promise.all([
      api('/api/bridges'),
      api('/api/director/extensions'),
    ]);
    const bridge = byId('link-bridge');
    const oldBridge = bridge.value || linkState.bridgeId;
    const onlineBridges = bridges.filter(row => row.connected);
    bridge.innerHTML = '<option value="">请选择在线 Bridge</option>' + onlineBridges
      .map(row => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)} · ${escapeHtml(row.machine_name)}</option>`)
      .join('');
    if (onlineBridges.some(row => row.id === oldBridge)) bridge.value = oldBridge;
    else if (onlineBridges.length === 1) bridge.value = onlineBridges[0].id;
    linkState.bridgeId = bridge.value;

    const extension = byId('link-extension');
    const oldExtension = extension.value || linkState.extensionId;
    const onlineExtensions = extensions.filter(row => row.connected);
    extension.innerHTML = '<option value="">可选：选择在线扩展</option>' + onlineExtensions
      .map(row => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)}</option>`).join('');
    if (onlineExtensions.some(row => row.id === oldExtension)) extension.value = oldExtension;
    else if (onlineExtensions.length === 1) extension.value = onlineExtensions[0].id;
    linkState.extensionId = extension.value;
  }

  function bridgeId() {
    const value = byId('link-bridge').value || linkState.bridgeId;
    if (!value) throw new Error('请先选择在线 Bridge');
    return value;
  }

  function renderList(id, rows, empty) {
    byId(id).innerHTML = rows?.length
      ? rows.map(row => `<li>${escapeHtml(row)}</li>`).join('')
      : `<li>${escapeHtml(empty)}</li>`;
  }

  function renderHistory(rows = []) {
    const body = byId('link-history-body');
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="10">等待采样。</td></tr>';
      return;
    }
    body.innerHTML = [...rows].reverse().map(row => {
      const rtc = row.rtc || {};
      const video = rtc.video || {};
      const local = row.aliver || {};
      const audio = row.audio || {};
      const diagnosis = row.diagnosis || {};
      const jitter = maxMetric(video.jitter_ms, (rtc.audio || {}).jitter_ms);
      const loss = maxMetric(video.packet_loss_pct, (rtc.audio || {}).packet_loss_pct);
      return `<tr>
        <td>${escapeHtml(shortTime(row.at_local))}</td>
        <td>${escapeHtml(metric(rtc.rtt_ms, 0, ' ms'))}</td>
        <td>${escapeHtml(metric(jitter, 0, ' ms'))}</td>
        <td>${escapeHtml(metric(loss, 2, '%'))}</td>
        <td>${escapeHtml(metric(video.decoded_fps ?? video.frames_per_second, 1))}</td>
        <td>${escapeHtml(metric(local.receive_fps, 1))}</td>
        <td>${escapeHtml(metric(local.render_fps, 1))}</td>
        <td>${escapeHtml(String(local.video_queue_size ?? '—'))}</td>
        <td>${escapeHtml(metric(audio.return_audio_buffer_ms, 0, ' ms'))}</td>
        <td>${escapeHtml(diagnosis.primary_bottleneck_zh || '—')}</td>
      </tr>`;
    }).join('');
  }

  function render(data) {
    const latest = data?.latest || null;
    linkState.sessionId = data?.session_id || '';
    byId('link-sample-count').textContent = `${data?.sample_count || 0} 个样本`;
    byId('link-event-path').textContent = data?.event_log_path || '—';
    byId('link-report-path').textContent = data?.report_path || '—';
    byId('link-live-json').textContent = JSON.stringify(data || {}, null, 2);
    renderHistory(data?.history_tail || []);

    if (!latest) {
      byId('link-conclusion').textContent = data?.message_zh || '尚未取得链路样本。';
      byId('link-conclusion').className = 'diagnosis warn';
      return;
    }

    const rtc = latest.rtc || {};
    const video = rtc.video || {};
    const rtcAudio = rtc.audio || {};
    const local = latest.aliver || {};
    const audio = latest.audio || {};
    const timeline = latest.timeline || {};
    const diagnosis = latest.diagnosis || {};
    const jitter = maxMetric(video.jitter_ms, rtcAudio.jitter_ms);
    const loss = maxMetric(video.packet_loss_pct, rtcAudio.packet_loss_pct);
    const decodedFps = video.decoded_fps ?? video.frames_per_second;

    const badge = byId('link-health-badge');
    badge.textContent = diagnosis.health === 'good' ? '链路正常' : diagnosis.health === 'bad' ? '发现瓶颈' : diagnosis.health === 'warning' ? '需要关注' : '数据不足';
    badge.className = `badge ${diagnosis.health === 'good' ? 'good' : diagnosis.health === 'bad' ? 'bad' : 'warn'}`;
    byId('link-conclusion').textContent = `${diagnosis.conclusion_zh || '正在分析。'} ${rtc.reason || ''}`.trim();
    byId('link-conclusion').className = `diagnosis ${diagnosis.health === 'good' ? 'good' : diagnosis.health === 'bad' ? 'bad' : 'warn'}`;

    byId('link-rtt').textContent = metric(rtc.rtt_ms, 0, ' ms');
    byId('link-route').textContent = `WebRTC 路径：${rtc.route || 'unknown'} · ${rtc.livekit_host || '未知节点'}`;
    byId('link-jitter-loss').textContent = `${metric(jitter, 0, ' ms')} / ${metric(loss, 2, '%')}`;
    byId('link-livekit-fps').textContent = `${metric(decodedFps, 1)} FPS`;
    byId('link-livekit-bitrate').textContent = `视频 ${metric(video.bitrate_kbps, 0, ' kbps')} · Jitter Buffer ${metric(video.jitter_buffer_avg_ms, 0, ' ms')}`;
    byId('link-aliver-fps').textContent = `${metric(local.receive_fps, 1)} → ${metric(local.render_fps, 1)} FPS`;
    byId('link-video-queue').textContent = `${local.video_queue_size ?? '—'} 帧`;
    byId('link-video-drops').textContent = `本区间队列丢 ${local.video_queue_drops_delta ?? 0} · 调度丢 ${local.video_render_drops_delta ?? 0}`;
    byId('link-audio-buffer').textContent = metric(audio.return_audio_buffer_ms, 0, ' ms');
    byId('link-waveout').textContent = `waveOut ${metric(audio.waveout_pending_ms, 0, ' ms')} · 欠载 +${audio.underflows_delta ?? 0}`;
    byId('link-gpt-out').textContent = metric(audio.gpt_out_dbfs, 1, ' dBFS');
    byId('link-input-queue').textContent = `输入队列 ${audio.simli_input_queue_chunks ?? 0} 块 · 已发送 ${audio.simli_sent_chunks ?? 0}`;
    byId('link-av-offset').textContent = metric(local.av_offset_ms, 0, ' ms');
    byId('link-lateness').textContent = `调度迟到 ${metric(local.scheduler_lateness_ms, 0, ' ms')}`;

    byId('flow-decode').textContent = `${metric(decodedFps, 1)} FPS`;
    byId('flow-receive').textContent = `${metric(local.receive_fps, 1)} FPS`;
    byId('flow-render').textContent = `${metric(local.render_fps, 1)} FPS`;

    byId('link-bottleneck').textContent = diagnosis.primary_bottleneck_zh || '未发现明确瓶颈';
    byId('link-bottleneck').className = `diagnosis ${diagnosis.health === 'bad' ? 'bad' : diagnosis.health === 'good' ? 'good' : 'warn'}`;
    renderList('link-evidence', diagnosis.evidence, '当前没有明确异常证据。');
    renderList('link-suggestions', diagnosis.suggestions, '继续运行 20 秒标准链路测试以获得更多样本。');

    byId('link-t-input').textContent = shortTime(timeline.first_non_silent_input_at);
    byId('link-t-send').textContent = shortTime(timeline.first_audio_sent_at);
    byId('link-t-send-delta').textContent = `输入→发送 ${metric(timeline.input_to_send_ms, 0, ' ms')}`;
    byId('link-t-return').textContent = shortTime(timeline.first_non_silent_return_audio_at);
    byId('link-t-return-delta').textContent = `输入→返回 ${metric(timeline.input_to_return_audio_ms, 0, ' ms')}`;
    byId('link-t-mouth').textContent = `${shortTime(timeline.first_video_rendered_at)} / ${shortTime(timeline.first_mouth_motion_at)}`;
    byId('link-t-mouth-delta').textContent = `返回声音→口型 ${metric(timeline.return_audio_to_mouth_ms, 0, ' ms')}`;
  }

  async function refresh() {
    const data = await sendBridgeCommand(
      bridgeId(),
      'provider.simli.link.get',
      { session_id: linkState.sessionId || null },
      6,
    );
    render(data);
    return data;
  }

  async function refreshEverything() {
    if (linkState.polling) return;
    linkState.polling = true;
    try {
      await loadSelectors();
      if (byId('link-bridge').value) await refresh();
    } finally {
      linkState.polling = false;
    }
  }

  async function runTest() {
    if (linkState.testRunning) return;
    if (!linkState.sessionId) await refresh();
    if (!linkState.sessionId) throw new Error('请先启动一个 Simli 会话');
    const extensionId = byId('link-extension').value;
    if (!extensionId) throw new Error('标准测试需要选择在线且已绑定语音会话的 Chrome 导演扩展');
    linkState.testRunning = true;
    const button = byId('link-test');
    const initial = button.textContent;
    button.disabled = true;
    button.textContent = '链路测试中 20 秒…';
    try {
      await api('/api/director/commands', {
        method: 'POST',
        body: JSON.stringify({
          extension_id: extensionId,
          command_type: 'director_instruction',
          content: TEST_TEXT,
          wrap_as_director: true,
          auto_send: true,
          force: false,
          priority: 95,
          source: 'simli_link_diagnostics',
        }),
      });
      toast('标准链路测试已发送。系统正在记录 RTC、帧率、队列和音频缓冲。');
      await new Promise(resolve => setTimeout(resolve, 20000));
      const report = await sendBridgeCommand(
        bridgeId(),
        'provider.simli.link.report',
        { session_id: linkState.sessionId },
        8,
      );
      render(report);
      toast(report.aggregate?.conclusion_zh || '链路测试完成');
    } finally {
      linkState.testRunning = false;
      button.disabled = false;
      button.textContent = initial;
    }
  }

  async function generateReport() {
    const report = await sendBridgeCommand(
      bridgeId(),
      'provider.simli.link.report',
      { session_id: linkState.sessionId || null },
      8,
    );
    render(report);
    toast(`链路报告已更新：${report.report_path || '已写入 Bridge diagnostics/link'}`);
  }

  async function generateBundle() {
    const result = await sendBridgeCommand(
      bridgeId(),
      'bridge.diagnostics.bundle',
      { reason: 'Simli 链路诊断页面手动导出', minutes: 180 },
      60,
    );
    toast(`故障包已生成：${result.bundle_path}`);
  }

  byId('link-bridge').addEventListener('change', () => {
    linkState.bridgeId = byId('link-bridge').value;
    linkState.sessionId = '';
    refresh().catch(error => toast(error.message, true));
  });
  byId('link-extension').addEventListener('change', () => { linkState.extensionId = byId('link-extension').value; });
  byId('link-refresh').addEventListener('click', () => refreshEverything().catch(error => toast(error.message, true)));
  byId('link-test').addEventListener('click', () => runTest().catch(error => toast(error.message, true)));
  byId('link-report').addEventListener('click', () => generateReport().catch(error => toast(error.message, true)));
  byId('link-bundle').addEventListener('click', () => generateBundle().catch(error => toast(error.message, true)));

  setInterval(() => {
    if (panel.classList.contains('active') && !linkState.polling && !linkState.testRunning) {
      refresh().catch(() => {});
    }
  }, 2000);
})();
