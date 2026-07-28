(() => {
  if (document.getElementById('tab-simli-tuning')) return;

  const STANDARD_TEST_TEXT = `请只用自然、正常的语速朗读下面的测试文本，不要解释，不要增加开场白或结束语。每段之间自然停顿大约一秒：\n\n同步测试开始。第一段，一二三四五，声音和口型应该同时出现。\n第二段，春风吹过安静的河面，远处的灯光慢慢亮起。\n第三段，数字人音画同步测试现在结束。`;
  const tuningState = {
    bridgeId: '',
    sessionId: '',
    status: null,
    latestReport: null,
    recommendation: null,
    polling: false,
  };

  const stylesheet = document.createElement('link');
  stylesheet.rel = 'stylesheet';
  stylesheet.href = '/static/simli_tuning.css?v=0.8.0';
  document.head.appendChild(stylesheet);

  const tabButton = document.createElement('button');
  tabButton.type = 'button';
  tabButton.dataset.tab = 'simli-tuning';
  tabButton.textContent = '数字人调试';
  const tabs = document.querySelector('.tabs');
  const logsButton = tabs?.querySelector('[data-tab="logs"]');
  if (logsButton) tabs.insertBefore(tabButton, logsButton);
  else tabs?.appendChild(tabButton);

  const panel = document.createElement('section');
  panel.id = 'tab-simli-tuning';
  panel.className = 'tab-panel';
  panel.innerHTML = `
    <article class="panel simli-tuning-hero">
      <div class="section-title">
        <div>
          <h2>Simli 音画同步与运动调试</h2>
          <p class="hint">用实际播放时间测量“声音到达扬声器”和“口型显示”的偏差；参数可实时应用，也可保存为这台 Bridge 的默认配置。</p>
        </div>
        <div class="actions">
          <span id="tuning-session-badge" class="badge warn">未连接</span>
          <button id="tuning-refresh" type="button" class="secondary">读取当前状态</button>
        </div>
      </div>
      <div class="tuning-selectors">
        <label>执行 Bridge
          <select id="tuning-bridge"><option value="">请选择在线 Bridge</option></select>
        </label>
        <label>标准测试使用的 Chrome 导演扩展
          <select id="tuning-extension"><option value="">请选择在线扩展</option></select>
        </label>
      </div>
      <div id="tuning-summary" class="diagnosis warn">启动 Simli 会话后读取状态。</div>
    </article>

    <section class="tuning-metrics">
      <article><span>实测口型偏差</span><strong id="tuning-offset">未测得</strong><small>正数=口型晚，负数=口型早</small></article>
      <article><span>视频时间速度</span><strong id="tuning-speed-ratio">未测得</strong><small>目标接近 1.00×</small></article>
      <article><span>源 / 渲染帧率</span><strong id="tuning-fps">未测得</strong><small>PTS FPS / 实际显示 FPS</small></article>
      <article><span>调度积压</span><strong id="tuning-lateness">未测得</strong><small>越接近 0 ms 越好</small></article>
      <article><span>音频设备延迟</span><strong id="tuning-audio-latency">未测得</strong><small>来自 Windows 播放设备</small></article>
    </section>

    <div class="grid two tuning-grid">
      <article class="panel">
        <div class="section-title">
          <div>
            <h2>播放参数</h2>
            <p class="hint">视频延迟、速度、时钟模式、帧率上限可在会话运行时生效。预缓冲修改建议在下次会话验证。</p>
          </div>
          <button id="tuning-reset" type="button" class="secondary">恢复推荐初始值</button>
        </div>
        <form id="tuning-form">
          <label>视频时钟模式
            <select name="clock_mode">
              <option value="source_pts">源 PTS（推荐，保持 Simli 原始动作速度）</option>
              <option value="arrival_clock">到达时钟（源时间戳异常时使用）</option>
              <option value="fixed_fps">固定帧率时钟（仅排障）</option>
            </select>
          </label>
          <div class="inline-fields tuning-main-fields">
            <label>视频延迟（ms）
              <input name="video_delay_ms" type="number" min="-5000" max="5000" step="10" value="0">
              <small>口型早于声音时增加；口型晚于声音时减少。</small>
            </label>
            <label>视频播放速度
              <input name="playback_speed" type="number" min="0.5" max="2" step="0.01" value="1">
              <small>1.00 为原速；只用于修正持续快放或慢放。</small>
            </label>
            <label>显示帧率上限
              <input name="target_fps" type="number" min="10" max="60" step="1" value="30">
              <small>限制显示负载，不用帧率数值强行改变动作时长。</small>
            </label>
          </div>
          <details class="tuning-advanced">
            <summary>高级缓冲与检测参数</summary>
            <div class="inline-fields tuning-advanced-fields">
              <label>启动预缓冲（ms）
                <input name="sync_prebuffer_ms" type="number" min="80" max="3000" step="10" value="350">
              </label>
              <label>过时视频丢帧阈值（ms）
                <input name="late_video_drop_ms" type="number" min="50" max="2000" step="10" value="250">
              </label>
              <label>有效语音阈值（dBFS）
                <input name="audio_active_dbfs" type="number" min="-75" max="-20" step="1" value="-50">
              </label>
              <label>口部检测灵敏度
                <input name="mouth_sensitivity" type="number" min="0.5" max="4" step="0.1" value="1">
              </label>
            </div>
          </details>
          <div class="actions tuning-actions">
            <button id="tuning-apply-live" type="button">应用到当前会话</button>
            <button id="tuning-save-default" type="button" class="secondary">保存为本机默认</button>
          </div>
          <div id="tuning-apply-result" class="diagnosis warn">尚未修改参数。</div>
        </form>
      </article>

      <article class="panel">
        <div class="section-title">
          <div>
            <h2>标准同步测试</h2>
            <p class="hint">系统先开始采样，再自动发送固定朗读指令。测试按真实墙钟记录声音和口型，不再把数字人的待机动作当成讲话。</p>
          </div>
          <label class="compact-field">测试时长（秒）
            <input id="tuning-test-duration" type="number" min="8" max="30" value="18">
          </label>
        </div>
        <div class="actions">
          <button id="tuning-run-test" type="button">发送标准指令并自动测量</button>
          <button id="tuning-fill-recommendation" type="button" class="secondary" disabled>填入推荐参数</button>
          <button id="tuning-apply-recommendation" type="button" class="secondary" disabled>一键应用可靠推荐</button>
        </div>
        <div id="tuning-test-progress" class="tuning-progress" hidden>
          <div id="tuning-test-progress-fill"></div>
        </div>
        <div id="tuning-test-conclusion" class="diagnosis warn">尚未进行标准同步测试。</div>
        <div id="tuning-recommendation" class="tuning-recommendation"><p class="hint">测试完成后显示推荐原因。</p></div>
        <pre id="tuning-test-json">暂无测试数据。</pre>
      </article>
    </div>

    <article class="panel">
      <div class="section-title">
        <div>
          <h2>实时运行数据与故障导出</h2>
          <p class="hint">状态每 1.5 秒刷新。出现卡顿、崩溃或明显不同步时，可直接生成包含当前参数和时间线的故障包。</p>
        </div>
        <button id="tuning-export-bundle" type="button" class="secondary">生成故障包</button>
      </div>
      <pre id="tuning-live-json">尚未读取。</pre>
    </article>
  `;
  document.querySelector('main')?.appendChild(panel);

  function activate() {
    document.querySelectorAll('.tabs button').forEach(button => button.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(item => item.classList.remove('active'));
    tabButton.classList.add('active');
    panel.classList.add('active');
    refreshEverything().catch(error => toast(error.message, true));
  }
  tabButton.addEventListener('click', activate);

  const byId = id => document.getElementById(id);
  const formatMetric = (value, digits = 1, suffix = '') => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '未测得';
    return `${Number(value).toFixed(digits)}${suffix}`;
  };

  function activeBridgeId() {
    const id = byId('tuning-bridge').value || tuningState.bridgeId;
    if (!id) throw new Error('请先选择在线 Bridge');
    return id;
  }

  function readForm() {
    const form = new FormData(byId('tuning-form'));
    return {
      clock_mode: form.get('clock_mode'),
      target_fps: Number(form.get('target_fps')),
      playback_speed: Number(form.get('playback_speed')),
      video_delay_ms: Number(form.get('video_delay_ms')),
      sync_prebuffer_ms: Number(form.get('sync_prebuffer_ms')),
      late_video_drop_ms: Number(form.get('late_video_drop_ms')),
      audio_active_dbfs: Number(form.get('audio_active_dbfs')),
      mouth_sensitivity: Number(form.get('mouth_sensitivity')),
    };
  }

  function writeForm(settings = {}) {
    const form = byId('tuning-form');
    Object.entries(settings).forEach(([key, value]) => {
      const input = form.elements.namedItem(key);
      if (input && value !== null && value !== undefined) input.value = value;
    });
  }

  function renderRecommendation(recommendation) {
    tuningState.recommendation = recommendation || null;
    const box = byId('tuning-recommendation');
    const fill = byId('tuning-fill-recommendation');
    const apply = byId('tuning-apply-recommendation');
    if (!recommendation?.settings) {
      box.innerHTML = '<p class="hint">尚无推荐参数。</p>';
      fill.disabled = true;
      apply.disabled = true;
      return;
    }
    box.innerHTML = `
      <h3>推荐参数</h3>
      <div class="tuning-recommendation-values">
        <span>时钟：<strong>${escapeHtml(recommendation.settings.clock_mode)}</strong></span>
        <span>视频延迟：<strong>${escapeHtml(recommendation.settings.video_delay_ms)} ms</strong></span>
        <span>速度：<strong>${escapeHtml(recommendation.settings.playback_speed)}×</strong></span>
        <span>帧率上限：<strong>${escapeHtml(recommendation.settings.target_fps)} FPS</strong></span>
      </div>
      <ul>${(recommendation.reasons || []).map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>
      <p class="hint">置信度：${escapeHtml(recommendation.confidence || 'insufficient')}；${recommendation.auto_apply_allowed ? '允许一键应用' : '数据置信度不足，仅建议人工填入后复测'}</p>
    `;
    fill.disabled = false;
    apply.disabled = !recommendation.auto_apply_allowed;
  }

  function renderReport(report) {
    tuningState.latestReport = report || null;
    if (!report) return;
    const offset = report.wall_lip_sync_offset_ms ?? report.wall_first_onset_offset_ms;
    byId('tuning-offset').textContent = formatMetric(offset, 0, ' ms');
    byId('tuning-speed-ratio').textContent = formatMetric(report.wall_video_speed_ratio, 2, '×');
    byId('tuning-fps').textContent = `${formatMetric(report.source_pts_fps, 1)} / ${formatMetric(report.render_fps_recent, 1)}`;
    byId('tuning-lateness').textContent = formatMetric(report.scheduler_lateness_ms, 0, ' ms');
    byId('tuning-audio-latency').textContent = formatMetric(report.audio_output_latency_ms, 1, ' ms');
    const conclusion = byId('tuning-test-conclusion');
    conclusion.textContent = report.conclusion_zh || '测试完成。';
    conclusion.className = `diagnosis ${offset !== null && offset !== undefined && Math.abs(Number(offset)) <= 120 ? 'good' : 'bad'}`;
    byId('tuning-test-json').textContent = JSON.stringify(report, null, 2);
    renderRecommendation(report.recommendation);
  }

  function renderStatus(data) {
    tuningState.status = data || {};
    tuningState.sessionId = data?.session_id || '';
    const badge = byId('tuning-session-badge');
    badge.textContent = data?.session_active ? '会话运行中' : '未运行会话';
    badge.className = `badge ${data?.session_active ? 'good' : 'warn'}`;
    if (data?.settings) writeForm(data.settings);
    const av = data?.av_sync || {};
    const latest = data?.latest_test || av?.tuning?.latest_test;
    if (latest) renderReport(latest);
    byId('tuning-live-json').textContent = JSON.stringify(data || {}, null, 2);
    const summary = byId('tuning-summary');
    if (!data?.session_active) {
      summary.textContent = `当前没有运行中的 Simli 会话。可以先保存本机默认参数；启动会话后再执行标准测试。配置文件：${data?.profile_path || '未上报'}`;
      summary.className = 'diagnosis warn';
      return;
    }
    const settings = data.settings || {};
    summary.textContent = `会话 ${data.session_id} · ${settings.clock_mode || '未知时钟'} · ${formatMetric(settings.target_fps, 0, ' FPS')} · ${formatMetric(settings.playback_speed, 2, '×')} · 视频延迟 ${formatMetric(settings.video_delay_ms, 0, ' ms')}`;
    summary.className = 'diagnosis good';
    byId('tuning-audio-latency').textContent = formatMetric(av.audio_output_latency_ms, 1, ' ms');
    if (!latest) {
      byId('tuning-fps').textContent = `${formatMetric(av.source_pts_fps, 1)} / ${formatMetric(av.render_fps_recent ?? av.render_fps, 1)}`;
      byId('tuning-lateness').textContent = formatMetric(av.scheduler_lateness_ms, 0, ' ms');
    }
  }

  async function loadSelectors() {
    const [bridges, extensions] = await Promise.all([
      api('/api/bridges'),
      api('/api/director/extensions'),
    ]);
    const bridgeSelect = byId('tuning-bridge');
    const previousBridge = bridgeSelect.value || tuningState.bridgeId;
    const onlineBridges = bridges.filter(row => row.connected);
    bridgeSelect.innerHTML = '<option value="">请选择在线 Bridge</option>' + onlineBridges
      .map(row => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)} · ${escapeHtml(row.machine_name)}</option>`)
      .join('');
    if (onlineBridges.some(row => row.id === previousBridge)) bridgeSelect.value = previousBridge;
    else if (onlineBridges.length === 1) bridgeSelect.value = onlineBridges[0].id;
    tuningState.bridgeId = bridgeSelect.value;

    const extensionSelect = byId('tuning-extension');
    const previousExtension = extensionSelect.value;
    const onlineExtensions = extensions.filter(row => row.connected);
    extensionSelect.innerHTML = '<option value="">请选择在线扩展</option>' + onlineExtensions
      .map(row => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)} · ${escapeHtml(row.browser_name)}</option>`)
      .join('');
    if (onlineExtensions.some(row => row.id === previousExtension)) extensionSelect.value = previousExtension;
    else if (onlineExtensions.length === 1) extensionSelect.value = onlineExtensions[0].id;
  }

  async function refreshStatus() {
    const bridgeId = activeBridgeId();
    tuningState.bridgeId = bridgeId;
    const data = await sendBridgeCommand(
      bridgeId,
      'provider.simli.tuning.get',
      { session_id: tuningState.sessionId || null },
      12,
    );
    renderStatus(data);
    return data;
  }

  async function refreshEverything() {
    if (tuningState.polling) return;
    tuningState.polling = true;
    try {
      await loadSelectors();
      if (byId('tuning-bridge').value) await refreshStatus();
    } finally {
      tuningState.polling = false;
    }
  }

  async function applySettings({ persist = false, settings = null } = {}) {
    const bridgeId = activeBridgeId();
    const result = await sendBridgeCommand(
      bridgeId,
      'provider.simli.tuning.apply',
      {
        session_id: tuningState.sessionId || null,
        settings: settings || readForm(),
        persist,
      },
      15,
    );
    renderStatus(result);
    const box = byId('tuning-apply-result');
    box.textContent = persist
      ? `参数已保存为本机默认。${result.session_active ? '当前会话也已实时应用。' : '下次启动会话时生效。'}`
      : `参数已应用到当前会话。${result.restart_recommended ? '预缓冲变更建议重启会话后复测。' : ''}`;
    box.className = 'diagnosis good';
    toast(box.textContent);
    return result;
  }

  async function resetSettings() {
    const result = await sendBridgeCommand(
      activeBridgeId(),
      'provider.simli.tuning.reset',
      { session_id: tuningState.sessionId || null, persist: true },
      15,
    );
    renderStatus(result);
    byId('tuning-apply-result').textContent = '已恢复并保存推荐初始值。';
    byId('tuning-apply-result').className = 'diagnosis good';
  }

  function startProgress(durationSeconds) {
    const progress = byId('tuning-test-progress');
    const fill = byId('tuning-test-progress-fill');
    progress.hidden = false;
    fill.style.width = '0%';
    const started = Date.now();
    const timer = setInterval(() => {
      const ratio = Math.min(1, (Date.now() - started) / (durationSeconds * 1000));
      fill.style.width = `${ratio * 100}%`;
      if (ratio >= 1) clearInterval(timer);
    }, 100);
    return () => {
      clearInterval(timer);
      fill.style.width = '100%';
      setTimeout(() => { progress.hidden = true; }, 800);
    };
  }

  async function runStandardTest() {
    const bridgeId = activeBridgeId();
    if (!tuningState.sessionId) await refreshStatus();
    if (!tuningState.sessionId) throw new Error('请先启动一个 Simli 会话');
    const extensionId = byId('tuning-extension').value;
    if (!extensionId) throw new Error('请选择在线 Chrome 导演扩展');
    const duration = Math.max(8, Math.min(30, Number(byId('tuning-test-duration').value || 18)));
    const button = byId('tuning-run-test');
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = `测试中 ${duration} 秒…`;
    const stopProgress = startProgress(duration);
    byId('tuning-test-conclusion').textContent = '正在记录真实声音输出与口型显示时间，请不要切换或关闭数字人窗口。';
    byId('tuning-test-conclusion').className = 'diagnosis warn';
    try {
      const measurement = sendBridgeCommand(
        bridgeId,
        'provider.simli.tuning.test',
        { session_id: tuningState.sessionId, duration_seconds: duration },
        duration + 20,
      );
      await new Promise(resolve => setTimeout(resolve, 500));
      await api('/api/director/commands', {
        method: 'POST',
        body: JSON.stringify({
          extension_id: extensionId,
          command_type: 'director_instruction',
          content: STANDARD_TEST_TEXT,
          wrap_as_director: true,
          auto_send: true,
          force: false,
          priority: 90,
          source: 'simli_tuning_test',
        }),
      });
      const report = await measurement;
      renderReport(report);
      toast(report.conclusion_zh || '标准同步测试完成');
    } finally {
      stopProgress();
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  async function exportBundle() {
    const result = await sendBridgeCommand(
      activeBridgeId(),
      'bridge.diagnostics.bundle',
      { reason: '数字人同步调试页面手动导出', minutes: 180 },
      60,
    );
    toast(`故障包已生成：${result.bundle_path}`);
    byId('tuning-live-json').textContent = JSON.stringify(result, null, 2);
  }

  byId('tuning-bridge').addEventListener('change', () => {
    tuningState.bridgeId = byId('tuning-bridge').value;
    tuningState.sessionId = '';
    refreshStatus().catch(error => toast(error.message, true));
  });
  byId('tuning-refresh').addEventListener('click', () => refreshEverything().catch(error => toast(error.message, true)));
  byId('tuning-apply-live').addEventListener('click', () => applySettings({ persist: false }).catch(error => toast(error.message, true)));
  byId('tuning-save-default').addEventListener('click', () => applySettings({ persist: true }).catch(error => toast(error.message, true)));
  byId('tuning-reset').addEventListener('click', () => resetSettings().catch(error => toast(error.message, true)));
  byId('tuning-run-test').addEventListener('click', () => runStandardTest().catch(error => toast(error.message, true)));
  byId('tuning-fill-recommendation').addEventListener('click', () => {
    if (tuningState.recommendation?.settings) {
      writeForm(tuningState.recommendation.settings);
      toast('推荐参数已填入，尚未应用。');
    }
  });
  byId('tuning-apply-recommendation').addEventListener('click', () => {
    if (!tuningState.recommendation?.settings) return;
    applySettings({ settings: tuningState.recommendation.settings, persist: false })
      .catch(error => toast(error.message, true));
  });
  byId('tuning-export-bundle').addEventListener('click', () => exportBundle().catch(error => toast(error.message, true)));

  setInterval(() => {
    if (panel.classList.contains('active')) refreshStatus().catch(() => {});
  }, 1500);
})();
