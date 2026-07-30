(() => {
  let installed = false;
  let localStatus = null;
  let serverStatus = null;
  let channelProbeResult = null;

  function extensionId() {
    return document.getElementById('auto-director-extension')?.value || '';
  }

  function connectedBridges() {
    return (state.bridges || []).filter(item => item.connected);
  }

  function selectedBridgeId() {
    return document.getElementById('douyin-visible-bridge')?.value
      || connectedBridges()[0]?.id
      || '';
  }

  function numberValue(id, fallback) {
    const value = Number(document.getElementById(id)?.value);
    return Number.isFinite(value) ? value : fallback;
  }

  function settingsFromForm() {
    return {
      extension_id: extensionId(),
      collector_id: 'douyin-visible-ui',
      mode: document.getElementById('douyin-visible-mode')?.value || 'hybrid',
      window_title_pattern: document.getElementById('douyin-visible-window')?.value || '.*直播伴侣.*',
      scan_interval_seconds: numberValue('douyin-visible-interval', 1.0),
      confidence_threshold: numberValue('douyin-visible-confidence', 0.72),
      uia_fallback_seconds: numberValue('douyin-visible-fallback', 4.0),
      wgc_frame_timeout_seconds: numberValue('douyin-visible-wgc-timeout', 3.0),
      enable_electron_accessibility: Boolean(document.getElementById('douyin-visible-electron')?.checked),
      enable_windows_graphics_capture: Boolean(document.getElementById('douyin-visible-wgc')?.checked),
      allow_screen_capture_fallback: Boolean(document.getElementById('douyin-visible-screen-fallback')?.checked),
      ocr_region: {
        x: numberValue('douyin-visible-region-x', 0.782),
        y: numberValue('douyin-visible-region-y', 0.405),
        width: numberValue('douyin-visible-region-width', 0.205),
        height: numberValue('douyin-visible-region-height', 0.555),
      },
      capture_comments: Boolean(document.getElementById('douyin-visible-comments')?.checked),
      capture_gifts: Boolean(document.getElementById('douyin-visible-gifts')?.checked),
      capture_follows: Boolean(document.getElementById('douyin-visible-follows')?.checked),
      capture_shares: Boolean(document.getElementById('douyin-visible-shares')?.checked),
      capture_likes: Boolean(document.getElementById('douyin-visible-likes')?.checked),
      capture_join_notices: Boolean(document.getElementById('douyin-visible-joins')?.checked),
    };
  }

  function fillBridgeOptions() {
    const select = document.getElementById('douyin-visible-bridge');
    if (!select) return;
    const current = select.value;
    select.innerHTML = connectedBridges().map(item => (
      `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.machine_name)}</option>`
    )).join('') || '<option value="">没有在线 Bridge</option>';
    if ([...select.options].some(option => option.value === current)) select.value = current;
  }

  function applyConfig(config = {}) {
    const set = (id, value) => {
      const element = document.getElementById(id);
      if (element && value !== undefined && value !== null && document.activeElement !== element) element.value = value;
    };
    set('douyin-visible-mode', config.mode);
    set('douyin-visible-window', config.window_title_pattern);
    set('douyin-visible-interval', config.scan_interval_seconds);
    set('douyin-visible-confidence', config.confidence_threshold);
    set('douyin-visible-fallback', config.uia_fallback_seconds);
    set('douyin-visible-wgc-timeout', config.wgc_frame_timeout_seconds);
    const region = config.ocr_region || {};
    set('douyin-visible-region-x', region.x);
    set('douyin-visible-region-y', region.y);
    set('douyin-visible-region-width', region.width);
    set('douyin-visible-region-height', region.height);
    for (const [id, key] of [
      ['douyin-visible-electron', 'enable_electron_accessibility'],
      ['douyin-visible-wgc', 'enable_windows_graphics_capture'],
      ['douyin-visible-screen-fallback', 'allow_screen_capture_fallback'],
      ['douyin-visible-comments', 'capture_comments'],
      ['douyin-visible-gifts', 'capture_gifts'],
      ['douyin-visible-follows', 'capture_follows'],
      ['douyin-visible-shares', 'capture_shares'],
      ['douyin-visible-likes', 'capture_likes'],
      ['douyin-visible-joins', 'capture_join_notices'],
    ]) {
      const element = document.getElementById(id);
      if (element && key in config && document.activeElement !== element) element.checked = Boolean(config[key]);
    }
  }

  function sourceLabel(value) {
    return ({
      uia: 'UI Automation',
      electron_accessibility: 'Electron Accessibility',
      windows_graphics_capture: 'Windows Graphics Capture + OCR',
      ocr: 'OCR',
      hybrid: '自动三级通道',
    })[value] || value || '未工作';
  }

  function channelCard(channel) {
    const name = sourceLabel(channel.channel);
    const error = channel.error ? `<small class="bad-text">${escapeHtml(channel.error)}</small>` : '';
    return `
      <article>
        <span>${escapeHtml(name)}</span>
        <strong>${channel.available === false ? '失败' : `${Number(channel.event_count || 0)} 事件`}</strong>
        <small>${Number(channel.line_count || 0)} 条文本</small>
        ${error}
      </article>
    `;
  }

  function render() {
    const badge = document.getElementById('douyin-collector-badge');
    const diagnosis = document.getElementById('douyin-collector-diagnosis');
    const metrics = document.getElementById('douyin-collector-metrics');
    const recent = document.getElementById('douyin-collector-recent');
    const raw = document.getElementById('douyin-collector-raw');
    const trace = document.getElementById('douyin-channel-trace');
    if (!badge || !diagnosis || !metrics || !recent || !raw) return;

    const running = Boolean(localStatus?.running);
    const connected = Boolean(serverStatus?.connected || localStatus?.connected);
    badge.textContent = running ? (connected ? '采集中' : '等待窗口') : '已停止';
    badge.className = `badge ${connected ? 'good' : running ? 'warn' : 'bad'}`;
    const error = localStatus?.last_error || serverStatus?.last_error;
    diagnosis.className = `diagnosis ${error ? 'bad' : connected ? 'ok' : 'warn'}`;
    diagnosis.textContent = error
      ? error
      : running
        ? `正在读取“${localStatus?.window?.title || '抖音直播伴侣'}” · 当前通道 ${sourceLabel(localStatus?.active_source)} · 自动启动已保存`
        : '采集器随 Windows Bridge 运行。优先级：UI Automation → Electron Accessibility → Windows Graphics Capture + OCR。';

    const counts = serverStatus?.counts || {};
    const electron = localStatus?.electron_accessibility || {};
    metrics.innerHTML = `
      <article><span>第一级 UIA</span><strong>${localStatus?.uia_available ? '可用' : '不可用'}</strong><small>系统可访问文本</small></article>
      <article><span>第二级 Electron</span><strong>${electron.enabled ? '已启用' : electron.available ? '需重启' : '不可用'}</strong><small>${escapeHtml(electron.message || 'Chromium 无障碍树')}</small></article>
      <article><span>第三级 WGC</span><strong>${localStatus?.wgc_available ? '可用' : '不可用'}</strong><small>窗口被遮挡时仍可捕获</small></article>
      <article><span>当前通道</span><strong>${escapeHtml(sourceLabel(localStatus?.active_source))}</strong><small>${escapeHtml(localStatus?.capture_source || '')}</small></article>
      <article><span>扫描</span><strong>${Number(localStatus?.scan_count || 0)}</strong><small>${Number(localStatus?.raw_line_count || 0)} 条文本</small></article>
      <article><span>进入导演</span><strong>${Number(serverStatus?.accepted || localStatus?.sent_count || 0)}</strong><small>已去重事件</small></article>
      <article><span>评论</span><strong>${Number(counts.comment || 0)}</strong><small>comment</small></article>
      <article><span>礼物</span><strong>${Number(counts.gift || 0)}</strong><small>gift</small></article>
      <article><span>关注</span><strong>${Number(counts.follow || 0)}</strong><small>follow</small></article>
      <article><span>重复</span><strong>${Number(serverStatus?.duplicates || localStatus?.duplicate_count || 0)}</strong><small>画面驻留去重</small></article>
    `;

    if (trace) {
      const rows = channelProbeResult?.channels || localStatus?.channel_trace || [];
      trace.innerHTML = rows.map(channelCard).join('') || '<p class="hint">尚未运行三级通道探针。</p>';
    }

    const events = (serverStatus?.recent || localStatus?.recent_events || []).slice().reverse();
    recent.innerHTML = events.map(item => `
      <div class="douyin-event-row">
        <div><strong>${escapeHtml(item.event_type || 'unknown')}</strong> · ${escapeHtml(item.user_name || '')}</div>
        <div>${escapeHtml(item.content || item.reason || '')}</div>
        <small>${escapeHtml(sourceLabel(item.source))}${item.confidence !== undefined ? ` · ${Number(item.confidence).toFixed(2)}` : ''}${item.score !== undefined ? ` · ${item.score}分` : ''} · ${formatTime(item.at || item.observed_at)}</small>
      </div>
    `).join('') || '<p class="hint">尚未识别到互动事件。</p>';

    raw.innerHTML = (localStatus?.recent_lines || []).slice().reverse().map(item => `
      <div class="douyin-event-row">
        <div><strong>${escapeHtml(sourceLabel(item.source))}</strong> · ${item.confidence !== undefined ? Number(item.confidence).toFixed(2) : '1.00'}</div>
        <div>${escapeHtml(item.text || '')}</div>
      </div>
    `).join('') || '<p class="hint">尚未读取到互动区文本。</p>';
  }

  async function bridgeCommand(command, payload = {}, timeout = 30) {
    const bridgeId = selectedBridgeId();
    if (!bridgeId) throw new Error('没有在线 Windows Bridge');
    return sendBridgeCommand(bridgeId, command, payload, timeout);
  }

  async function refreshStatus() {
    fillBridgeOptions();
    const bridgeId = selectedBridgeId();
    const id = extensionId();
    if (bridgeId) {
      try {
        localStatus = await bridgeCommand('douyin.visible.status', {}, 15);
        applyConfig(localStatus.config || {});
      } catch (error) {
        localStatus = { last_error: error.message };
      }
    }
    if (id) {
      try {
        serverStatus = await api(`/api/douyin-live/status?extension_id=${encodeURIComponent(id)}`);
      } catch (error) {
        serverStatus = { last_error: error.message };
      }
    }
    render();
  }

  async function startCollector() {
    if (!extensionId()) throw new Error('请先选择 Chrome 导演扩展并保存自动导演配置');
    const settings = settingsFromForm();
    localStatus = await bridgeCommand('douyin.visible.start', { settings }, 60);
    applyConfig(localStatus.config || {});
    toast('抖音三级互动采集器已启动，并保存为 Bridge 自动启动配置');
    await refreshStatus();
  }

  async function stopCollector() {
    localStatus = await bridgeCommand('douyin.visible.stop', {}, 20);
    toast('抖音可视互动采集器已停止');
    await refreshStatus();
  }

  async function scanOnce() {
    localStatus = await bridgeCommand('douyin.visible.scan_once', {}, 90);
    toast(`扫描完成，使用通道：${sourceLabel(localStatus.active_source)}`);
    await refreshStatus();
  }

  async function calibrate() {
    localStatus = await bridgeCommand('douyin.visible.calibrate_default', {}, 30);
    applyConfig(localStatus.config || {});
    toast('已按当前直播伴侣布局自动定位右侧“互动消息”区域');
    await refreshStatus();
  }

  async function probeChannels() {
    channelProbeResult = await bridgeCommand('douyin.visible.channel_probe', {}, 120);
    const usable = (channelProbeResult.channels || []).filter(item => item.available !== false);
    toast(`三级通道探针完成：${usable.map(item => sourceLabel(item.channel)).join('、') || '没有可用通道'}`);
    await refreshStatus();
  }

  async function restartAccessibility() {
    const confirmed = window.confirm(
      '此操作会向直播伴侣发送正常关闭请求，并使用 --force-renderer-accessibility 重新启动。\n\n请只在未开播时执行；不会强制结束进程。是否继续？',
    );
    if (!confirmed) return;
    const result = await bridgeCommand('douyin.visible.electron_accessibility.restart', {}, 60);
    toast(result.enabled
      ? '直播伴侣已用 Electron Accessibility 模式重启'
      : (result.message || '已发起无障碍模式重启'));
    await refreshStatus();
  }

  async function simulate() {
    const id = extensionId();
    if (!id) throw new Error('请先选择 Chrome 导演扩展');
    const result = await api('/api/douyin-live/simulate', {
      method: 'POST',
      body: JSON.stringify({ extension_id: id, collector_id: 'douyin-visible-ui-simulator' }),
    });
    toast(`已模拟 ${result.accepted} 条可视互动事件`);
    await refreshStatus();
    if (typeof loadAutoDirector === 'function') await loadAutoDirector();
  }

  function bind(id, handler) {
    document.getElementById(id)?.addEventListener('click', () => {
      handler().catch(error => toast(error.message, true));
    });
  }

  function install() {
    if (installed) return true;
    const tab = document.getElementById('tab-auto-director');
    const eventForm = document.getElementById('auto-director-event-form');
    if (!tab || !eventForm) return false;
    installed = true;

    const article = document.createElement('article');
    article.id = 'douyin-live-collector-panel';
    article.className = 'panel douyin-live-collector-panel';
    article.innerHTML = `
      <div class="section-title">
        <div>
          <h2>抖音直播伴侣三级互动采集器</h2>
          <p class="hint">自动顺序：UI Automation → Electron Accessibility → Windows Graphics Capture + OCR。不会抓包、注入进程、读取 Cookie 或调用抖音私有接口。</p>
        </div>
        <span id="douyin-collector-badge" class="badge warn">检查中</span>
      </div>
      <div class="grid two douyin-visible-grid">
        <div>
          <label>执行 Bridge<select id="douyin-visible-bridge"><option value="">等待在线 Bridge</option></select></label>
          <label>采集模式<select id="douyin-visible-mode"><option value="hybrid">自动三级通道</option><option value="uia">仅 UI Automation</option><option value="electron">仅 Electron Accessibility</option><option value="wgc">仅 Windows Graphics Capture + OCR</option></select></label>
          <label>窗口标题正则<input id="douyin-visible-window" value=".*直播伴侣.*"></label>
          <div class="inline-fields">
            <label>扫描间隔（秒）<input id="douyin-visible-interval" type="number" min="0.4" max="10" step="0.1" value="1"></label>
            <label>OCR 置信度<input id="douyin-visible-confidence" type="number" min="0.3" max="0.99" step="0.01" value="0.72"></label>
            <label>WGC 等待（秒）<input id="douyin-visible-wgc-timeout" type="number" min="0.5" max="10" step="0.5" value="3"></label>
          </div>
          <label hidden>旧版 UIA 回退秒数<input id="douyin-visible-fallback" type="number" value="4"></label>
          <div class="collector-checks">
            <label class="check-row"><input id="douyin-visible-electron" type="checkbox" checked>启用 Electron Accessibility</label>
            <label class="check-row"><input id="douyin-visible-wgc" type="checkbox" checked>启用 Windows Graphics Capture</label>
            <label class="check-row"><input id="douyin-visible-screen-fallback" type="checkbox">允许桌面截图兼容兜底</label>
          </div>
          <p class="hint">桌面截图兜底默认关闭，避免把其他窗口文字误识别成弹幕。</p>
        </div>
        <div>
          <strong>互动消息 OCR 区域（相对直播伴侣窗口）</strong>
          <div class="inline-fields">
            <label>X<input id="douyin-visible-region-x" type="number" min="0" max="1" step="0.001" value="0.782"></label>
            <label>Y<input id="douyin-visible-region-y" type="number" min="0" max="1" step="0.001" value="0.405"></label>
            <label>宽<input id="douyin-visible-region-width" type="number" min="0.02" max="1" step="0.001" value="0.205"></label>
            <label>高<input id="douyin-visible-region-height" type="number" min="0.02" max="1" step="0.001" value="0.555"></label>
          </div>
          <div class="collector-checks">
            <label class="check-row"><input id="douyin-visible-comments" type="checkbox" checked>评论</label>
            <label class="check-row"><input id="douyin-visible-gifts" type="checkbox" checked>礼物</label>
            <label class="check-row"><input id="douyin-visible-follows" type="checkbox" checked>关注</label>
            <label class="check-row"><input id="douyin-visible-shares" type="checkbox" checked>分享提示</label>
            <label class="check-row"><input id="douyin-visible-likes" type="checkbox">单条点赞</label>
            <label class="check-row"><input id="douyin-visible-joins" type="checkbox">观众进入</label>
          </div>
        </div>
      </div>
      <div class="actions">
        <button id="douyin-visible-calibrate" type="button" class="secondary">自动校准互动区</button>
        <button id="douyin-visible-start" type="button">保存并启动</button>
        <button id="douyin-visible-stop" type="button" class="danger">停止采集</button>
        <button id="douyin-visible-scan" type="button" class="secondary">立即扫描一次</button>
        <button id="douyin-channel-probe" type="button" class="secondary">测试三级通道</button>
        <button id="douyin-electron-restart" type="button" class="secondary">无障碍模式重启直播伴侣</button>
        <button id="douyin-collector-simulate" type="button" class="secondary">模拟完整链路</button>
        <button id="douyin-collector-refresh" type="button" class="secondary">刷新状态</button>
      </div>
      <div id="douyin-collector-diagnosis" class="diagnosis warn">正在读取 Bridge 状态。</div>
      <div id="douyin-collector-metrics" class="douyin-collector-metrics"></div>
      <details><summary>三级通道探针结果</summary><div id="douyin-channel-trace" class="douyin-collector-metrics"><p class="hint">尚未运行三级通道探针。</p></div></details>
      <div class="grid two">
        <details open><summary>解析后的互动事件</summary><div id="douyin-collector-recent" class="douyin-collector-recent"><p class="hint">尚未识别到互动事件。</p></div></details>
        <details><summary>三级通道原始文本</summary><div id="douyin-collector-raw" class="douyin-collector-recent"><p class="hint">尚未读取文本。</p></div></details>
      </div>
      <div class="douyin-collector-notes">
        <p>第一级直接读取 Windows UIA；第二级需在未开播时用无障碍参数重启直播伴侣；第三级通过 Windows Graphics Capture 捕获窗口，可被其他窗口遮挡，但不保证最小化后继续渲染。</p>
        <p>直播伴侣没有在界面显示的事件无法采集；礼物纯动画若无文字不会被伪造为事件。</p>
      </div>
    `;
    eventForm.closest('.panel')?.insertAdjacentElement('beforebegin', article);

    bind('douyin-visible-start', startCollector);
    bind('douyin-visible-stop', stopCollector);
    bind('douyin-visible-scan', scanOnce);
    bind('douyin-visible-calibrate', calibrate);
    bind('douyin-channel-probe', probeChannels);
    bind('douyin-electron-restart', restartAccessibility);
    bind('douyin-collector-simulate', simulate);
    bind('douyin-collector-refresh', refreshStatus);
    document.getElementById('auto-director-extension')?.addEventListener('change', () => setTimeout(refreshStatus, 100));
    refreshStatus().catch(() => {});
    return true;
  }

  if (!install()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (install() || attempts > 100) clearInterval(timer);
    }, 200);
  }

  setInterval(() => {
    if (document.getElementById('tab-auto-director')?.classList.contains('active')) refreshStatus().catch(() => {});
  }, 3000);
})();
