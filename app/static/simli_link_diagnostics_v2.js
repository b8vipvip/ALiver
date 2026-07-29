(() => {
  const oldPanel = document.getElementById('tab-simli-link');
  if (oldPanel) {
    oldPanel.classList.remove('active');
    oldPanel.remove();
  }
  document.querySelectorAll('[data-tab="simli-link"]').forEach(button => {
    button.classList.remove('active');
    button.remove();
  });
  if (document.getElementById('tab-simli-link-v2')) return;

  const TEST_TEXT = `请用正常、自然、连续的语速说大约十五秒：大家好，欢迎来到直播间。现在正在进行数字人实时链路测试，请保持正常语速，不要唱歌，也不要刻意放慢。今天我们会聊一些轻松有趣的话题，欢迎大家在评论区一起互动。测试马上结束，谢谢大家。`;
  const linkState = {
    bridgeId: '',
    activeSessionId: '',
    pinnedTestSessionId: '',
    extensionId: '',
    polling: false,
    testRunning: false,
  };

  if (!document.querySelector('link[href*="simli_link_diagnostics.css"]')) {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/simli_link_diagnostics.css?v=0.9.1';
    document.head.appendChild(css);
  }

  const tabButton = document.createElement('button');
  tabButton.type = 'button';
  tabButton.dataset.tab = 'simli-link-v2';
  tabButton.textContent = '链路诊断';
  const tabs = document.querySelector('.tabs');
  const logsButton = tabs?.querySelector('[data-tab="logs"]');
  if (logsButton) tabs.insertBefore(tabButton, logsButton);
  else tabs?.appendChild(tabButton);

  const panel = document.createElement('section');
  panel.id = 'tab-simli-link-v2';
  panel.className = 'tab-panel';
  panel.innerHTML = `
    <article class="panel link-diagnostics-hero">
      <div class="section-title">
        <div>
          <h2>Simli 实时链路诊断 v2</h2>
          <p class="hint">使用同一采样区间比较 RTC 解码、ALiver 收帧和窗口渲染；arrival burst 只用于观察突发到帧，不再用于判定本地渲染瓶颈。</p>
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
      <div id="link-conclusion" class="diagnosis warn">启动 Simli 会话后，系统会自动跟随当前活动会话。</div>
    </article>

    <section class="link-health-grid">
      <article><span>网络 RTT</span><strong id="link-rtt">—</strong><small id="link-route">WebRTC 路径：—</small></article>
      <article><span>网络抖动 / 丢包</span><strong id="link-jitter-loss">—</strong><small id="link-jitter-buffer">Jitter Buffer：—</small></article>
      <article><span>LiveKit 区间解码</span><strong id="link-livekit-fps">—</strong><small id="link-livekit-bitrate">解码 FPS / 接收码率</small></article>
      <article><span>ALiver 区间帧链</span><strong id="link-aliver-fps">—</strong><small>同一约 2 秒窗口：收帧 → 渲染</small></article>
      <article><span>源 PTS / 到帧突发</span><strong id="link-source-burst">—</strong><small>突发值不参与本地瓶颈判定</small></article>
      <article><span>视频队列</span><strong id="link-video-queue">—</strong><small id="link-video-drops">积压增长 / 丢帧</small></article>
      <article><span>返回音频缓冲</span><strong id="link-audio-buffer">—</strong><small id="link-waveout">Simli 队列 / Windows 播放队列</small></article>
      <article><span>待机媒体裁剪</span><strong id="link-idle-trim">—</strong><small id="link-idle-trim-detail">新讲话前裁掉旧待机媒体</small></article>
      <article><span>GPT_OUT 输入</span><strong id="link-gpt-out">—</strong><small id="link-input-queue">送往 Simli 的本地队列</small></article>
      <article><span>A/V 调度</span><strong id="link-av-offset">—</strong><small id="link-lateness">当前偏差 / 调度迟到</small></article>
    </section>

    <div class="grid two">
      <article class="panel">
        <h2>自动瓶颈判断</h2>
        <div id="link-bottleneck" class="diagnosis warn">尚无足够数据。</div>
        <h3>并存问题</h3>
        <ul id="link-issues" class="link-diagnosis-list"><li>等待实时采样。</li></ul>
        <h3>证据</h3>
        <ul id="link-evidence" class="link-diagnosis-list"><li>等待实时采样。</li></ul>
        <h3>建议</h3>
        <ul id="link-suggestions" class="link-diagnosis-list"><li>等待实时采样。</li></ul>
      </article>
      <article class="panel">
        <h2>本轮标准测试时间线</h2>
        <div class="link-stage-grid">
          <article><span>GPT_OUT 首次有效语音</span><strong id="link-t-input">—</strong><small>只统计 test epoch 之后</small></article>
          <article><span>首次送入 Simli</span><strong id="link-t-send">—</strong><small id="link-t-send-delta">输入→发送</small></article>
          <article><span>Simli 返回语音</span><strong id="link-t-return">—</strong><small id="link-t-return-delta">输入→返回语音</small></article>
          <article><span>首帧 / 首次口型</span><strong id="link-t-mouth">—</strong><small id="link-t-mouth-delta">返回声音→口型</small></article>
        </div>
        <div id="link-network-policy" class="diagnosis warn">网络策略：等待会话。</div>
      </article>
    </div>

    <article class="panel">
      <div class="section-title">
        <div><h2>同区间三级视频吞吐</h2><p class="hint">RTC Decode → ALiver interval receive → ALiver interval render。三项必须来自接近相同的采样窗口。</p></div>
      </div>
      <div class="link-flow">
        <div class="flow-node"><span>① LiveKit 解码</span><strong id="flow-decode">— FPS</strong><small>网络/Simli 到 WebRTC 解码层</small></div>
        <div class="flow-node"><span>② ALiver 区间取帧</span><strong id="flow-receive">— FPS</strong><small>实际帧计数差值 / elapsed</small></div>
        <div class="flow-node"><span>③ OpenCV 区间显示</span><strong id="flow-render">— FPS</strong><small>实际渲染计数差值 / elapsed</small></div>
      </div>
    </article>

    <article class="panel">
      <div class="section-title"><h2>最近实时样本</h2><span id="link-sample-count" class="hint">0 个样本</span></div>
      <div class="link-history"><table><thead><tr>
        <th>时间</th><th>RTT</th><th>Loss</th><th>Decode</th><th>Receive</th><th>Render</th><th>Burst</th><th>Queue</th><th>Audio</th><th>判断</th>
      </tr></thead><tbody id="link-history-body"><tr><td colspan="10">等待采样。</td></tr></tbody></table></div>
    </article>

    <article class="panel link-paths">
      <div class="section-title"><h2>自动日志与原始数据</h2><span class="hint">报告使用临时文件 + os.replace 原子更新。</span></div>
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
    const rows = values
      .filter(value => value !== null && value !== undefined && !Number.isNaN(Number(value)))
      .map(Number);
    return rows.length ? Math.max(...rows) : null;
  };
  const list = (id, rows, empty) => {
    byId(id).innerHTML = rows?.length
      ? rows.map(row => `<li>${escapeHtml(row)}</li>`).join('')
      : `<li>${escapeHtml(empty)}</li>`;
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
      .map(row => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)}</option>`)
      .join('');
    if (onlineExtensions.some(row => row.id === oldExtension)) extension.value = oldExtension;
    else if (onlineExtensions.length === 1) extension.value = onlineExtensions[0].id;
    linkState.extensionId = extension.value;
  }

  function bridgeId() {
    const value = byId('link-bridge').value || linkState.bridgeId;
    if (!value) throw new Error('请先选择在线 Bridge');
    return value;
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
      const loss = maxMetric(video.packet_loss_pct, (rtc.audio || {}).packet_loss_pct);
      return `<tr>
        <td>${escapeHtml(shortTime(row.at_local))}</td>
        <td>${escapeHtml(metric(rtc.rtt_ms, 0, ' ms'))}</td>
        <td>${escapeHtml(metric(loss, 2, '%'))}</td>
        <td>${escapeHtml(metric(video.decoded_fps ?? video.frames_per_second, 1))}</td>
        <td>${escapeHtml(metric(local.receive_fps, 1))}</td>
        <td>${escapeHtml(metric(local.render_fps, 1))}</td>
        <td>${escapeHtml(metric(local.arrival_burst_fps, 1))}</td>
        <td>${escapeHtml(String(local.video_queue_size ?? '—'))}</td>
        <td>${escapeHtml(metric(audio.return_audio_buffer_ms, 0, ' ms'))}</td>
        <td>${escapeHtml(diagnosis.primary_bottleneck_zh || '—')}</td>
      </tr>`;
    }).join('');
  }

  function clearMetrics(message) {
    linkState.activeSessionId = '';
    byId('link-health-badge').textContent = '等待会话';
    byId('link-health-badge').className = 'badge warn';
    byId('link-conclusion').textContent = message || '当前 Bridge 没有运行中的 Simli 会话。';
    byId('link-conclusion').className = 'diagnosis warn';
    [
      'link-rtt', 'link-jitter-loss', 'link-livekit-fps', 'link-aliver-fps',
      'link-source-burst', 'link-video-queue', 'link-audio-buffer', 'link-idle-trim',
      'link-gpt-out', 'link-av-offset', 'flow-decode', 'flow-receive', 'flow-render',
      'link-t-input', 'link-t-send', 'link-t-return', 'link-t-mouth',
    ].forEach(id => { byId(id).textContent = '—'; });
    byId('link-route').textContent = 'WebRTC 路径：—';
    byId('link-jitter-buffer').textContent = 'Jitter Buffer：—';
    byId('link-livekit-bitrate').textContent = '解码 FPS / 接收码率';
    byId('link-video-drops').textContent = '积压增长 / 丢帧';
    byId('link-waveout').textContent = 'Simli 队列 / Windows 播放队列';
    byId('link-idle-trim-detail').textContent = '新讲话前裁掉旧待机媒体';
    byId('link-input-queue').textContent = '送往 Simli 的本地队列';
    byId('link-lateness').textContent = '当前偏差 / 调度迟到';
    byId('link-t-send-delta').textContent = '输入→发送 —';
    byId('link-t-return-delta').textContent = '输入→返回 —';
    byId('link-t-mouth-delta').textContent = '返回声音→口型 —';
    byId('link-bottleneck').textContent = '尚无足够数据。';
    byId('link-bottleneck').className = 'diagnosis warn';
    byId('link-network-policy').textContent = '网络策略：等待会话。';
    byId('link-network-policy').className = 'diagnosis warn';
    list('link-issues', [], '等待实时采样。');
    list('link-evidence', [], '等待实时采样。');
    list('link-suggestions', [], '等待实时采样。');
    renderHistory([]);
  }

  function render(data) {
    const latest = data?.latest || null;
    byId('link-sample-count').textContent = `${data?.sample_count || 0} 个样本`;
    byId('link-event-path').textContent = data?.event_log_path || '—';
    byId('link-report-path').textContent = data?.report_path || '—';
    byId('link-live-json').textContent = JSON.stringify(data || {}, null, 2);
    renderHistory(data?.history_tail || []);

    if (!latest) {
      clearMetrics(data?.message_zh || '尚未取得链路样本。');
      byId('link-live-json').textContent = JSON.stringify(data || {}, null, 2);
      return;
    }

    linkState.activeSessionId = data.session_id || latest.session_id || '';
    const rtc = latest.rtc || {};
    const video = rtc.video || {};
    const rtcAudio = rtc.audio || {};
    const local = latest.aliver || {};
    const audio = latest.audio || {};
    const timeline = latest.timeline || {};
    const diagnosis = latest.diagnosis || {};
    const jitter = maxMetric(video.jitter_ms, rtcAudio.jitter_ms);
    const loss = maxMetric(video.packet_loss_pct, rtcAudio.packet_loss_pct);
    const jitterBuffer = maxMetric(video.jitter_buffer_avg_ms, rtcAudio.jitter_buffer_avg_ms);
    const decodedFps = video.decoded_fps ?? video.frames_per_second;

    const badge = byId('link-health-badge');
    badge.textContent = diagnosis.health === 'good'
      ? '链路正常'
      : diagnosis.health === 'bad'
        ? '发现瓶颈'
        : diagnosis.health === 'warning' ? '需要关注' : '数据不足';
    badge.className = `badge ${diagnosis.health === 'good' ? 'good' : diagnosis.health === 'bad' ? 'bad' : 'warn'}`;
    byId('link-conclusion').textContent = `${diagnosis.conclusion_zh || '正在分析。'} ${data.session_switched ? '已自动切换到当前活动会话。' : ''}`.trim();
    byId('link-conclusion').className = `diagnosis ${diagnosis.health === 'good' ? 'good' : diagnosis.health === 'bad' ? 'bad' : 'warn'}`;

    byId('link-rtt').textContent = metric(rtc.rtt_ms, 0, ' ms');
    byId('link-route').textContent = `WebRTC 路径：${rtc.route || 'unknown'} · ${rtc.livekit_host || '未知节点'}`;
    byId('link-jitter-loss').textContent = `${metric(jitter, 0, ' ms')} / ${metric(loss, 2, '%')}`;
    byId('link-jitter-buffer').textContent = `Jitter Buffer：${metric(jitterBuffer, 0, ' ms')}`;
    byId('link-livekit-fps').textContent = `${metric(decodedFps, 1)} FPS`;
    byId('link-livekit-bitrate').textContent = `视频 ${metric(video.bitrate_kbps, 0, ' kbps')} · ${video.frame_width || '—'}×${video.frame_height || '—'}`;
    byId('link-aliver-fps').textContent = `${metric(local.receive_fps, 1)} → ${metric(local.render_fps, 1)} FPS`;
    byId('link-source-burst').textContent = `${metric(local.source_pts_fps, 1)} / ${metric(local.arrival_burst_fps, 1)} FPS`;
    byId('link-video-queue').textContent = `${local.video_queue_size ?? '—'} 帧`;
    byId('link-video-drops').textContent = `本区间增长 ${local.video_queue_growth ?? 0} · 队列丢 ${local.video_queue_drops_delta ?? 0} · 调度丢 ${local.video_render_drops_delta ?? 0}`;
    byId('link-audio-buffer').textContent = metric(audio.return_audio_buffer_ms, 0, ' ms');
    byId('link-waveout').textContent = `waveOut ${metric(audio.waveout_pending_ms, 0, ' ms')} · 欠载 +${audio.underflows_delta ?? 0}`;
    byId('link-idle-trim').textContent = `${audio.idle_trim_count ?? 0} 次`;
    byId('link-idle-trim-detail').textContent = `累计裁音频 ${metric(audio.idle_trim_audio_ms_total, 0, ' ms')} · 最近裁 ${metric(audio.idle_trim_last_audio_ms, 0, ' ms')} · 裁后 ${metric(audio.idle_trim_post_audio_buffer_ms, 0, ' ms')}`;
    byId('link-gpt-out').textContent = metric(audio.gpt_out_dbfs, 1, ' dBFS');
    byId('link-input-queue').textContent = `输入队列 ${audio.simli_input_queue_chunks ?? 0} 块 · 已发送 ${audio.simli_sent_chunks ?? 0}`;
    byId('link-av-offset').textContent = metric(local.av_offset_ms, 0, ' ms');
    byId('link-lateness').textContent = `调度迟到 ${metric(local.scheduler_lateness_ms, 0, ' ms')}`;

    byId('flow-decode').textContent = `${metric(decodedFps, 1)} FPS`;
    byId('flow-receive').textContent = `${metric(local.receive_fps, 1)} FPS`;
    byId('flow-render').textContent = `${metric(local.render_fps, 1)} FPS`;

    byId('link-bottleneck').textContent = diagnosis.primary_bottleneck_zh || '未发现明确瓶颈';
    byId('link-bottleneck').className = `diagnosis ${diagnosis.health === 'bad' ? 'bad' : diagnosis.health === 'good' ? 'good' : 'warn'}`;
    list('link-issues', (diagnosis.issues || []).map(issue => `${issue.label_zh}（分数 ${issue.score}）`), '当前没有并存问题。');
    list('link-evidence', diagnosis.evidence, '当前没有明确异常证据。');
    list('link-suggestions', diagnosis.suggestions, '继续运行标准测试以获得更多样本。');

    byId('link-t-input').textContent = shortTime(timeline.first_non_silent_input_at);
    byId('link-t-send').textContent = shortTime(timeline.first_audio_sent_at);
    byId('link-t-send-delta').textContent = `输入→发送 ${metric(timeline.input_to_send_ms, 0, ' ms')}`;
    byId('link-t-return').textContent = shortTime(timeline.first_non_silent_return_audio_at);
    byId('link-t-return-delta').textContent = `输入→返回 ${metric(timeline.input_to_return_audio_ms, 0, ' ms')}`;
    byId('link-t-mouth').textContent = `${shortTime(timeline.first_video_rendered_at)} / ${shortTime(timeline.first_mouth_motion_at)}`;
    byId('link-t-mouth-delta').textContent = `返回声音→口型 ${metric(timeline.return_audio_to_mouth_ms, 0, ' ms')}`;

    const policy = latest.network_policy || {};
    byId('link-network-policy').textContent = `网络策略：${policy.mode || 'inherit'}。${policy.note_zh || ''}`;
    byId('link-network-policy').className = `diagnosis ${policy.mode === 'direct_env' ? 'good' : 'warn'}`;
  }

  async function refresh() {
    const data = await sendBridgeCommand(
      bridgeId(),
      'provider.simli.link.get',
      { session_id: null },
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
    const extensionId = byId('link-extension').value;
    if (!extensionId) throw new Error('标准测试需要选择在线且已绑定语音会话的 Chrome 导演扩展');
    const test = await sendBridgeCommand(
      bridgeId(),
      'provider.simli.link.test.begin',
      { session_id: null },
      8,
    );
    linkState.pinnedTestSessionId = test.session_id;
    if (!linkState.pinnedTestSessionId) throw new Error('请先启动一个 Simli 会话');

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
          source: 'simli_link_diagnostics_v2',
        }),
      });
      toast(`标准测试已开始：${test.test_id}。时间线只统计本轮测试之后的事件。`);
      await new Promise(resolve => setTimeout(resolve, 20000));
      const report = await sendBridgeCommand(
        bridgeId(),
        'provider.simli.link.report',
        { session_id: linkState.pinnedTestSessionId },
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
      { session_id: linkState.activeSessionId || null },
      8,
    );
    render(report);
    toast(`链路报告已原子更新：${report.report_path || 'Bridge diagnostics/link'}`);
  }

  async function generateBundle() {
    const result = await sendBridgeCommand(
      bridgeId(),
      'bridge.diagnostics.bundle',
      { reason: 'Simli 链路诊断 v2 页面手动导出', minutes: 180 },
      60,
    );
    toast(`故障包已生成：${result.bundle_path}`);
  }

  byId('link-bridge').addEventListener('change', () => {
    linkState.bridgeId = byId('link-bridge').value;
    linkState.activeSessionId = '';
    linkState.pinnedTestSessionId = '';
    refresh().catch(error => toast(error.message, true));
  });
  byId('link-extension').addEventListener('change', () => {
    linkState.extensionId = byId('link-extension').value;
  });
  byId('link-refresh').addEventListener('click', () => refreshEverything().catch(error => toast(error.message, true)));
  byId('link-test').addEventListener('click', () => runTest().catch(error => toast(error.message, true)));
  byId('link-report').addEventListener('click', () => generateReport().catch(error => toast(error.message, true)));
  byId('link-bundle').addEventListener('click', () => generateBundle().catch(error => toast(error.message, true)));

  clearMetrics('启动 Simli 会话后，系统会自动跟随当前活动会话。');
  setInterval(() => {
    if (panel.classList.contains('active') && !linkState.polling && !linkState.testRunning) {
      refresh().catch(() => {});
    }
  }, 2000);
})();
