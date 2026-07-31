(() => {
  const state = { status: null, runs: [], busy: false, mounted: false };

  function formatDuration(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const remain = Math.floor(value % 60);
    return hours ? `${hours}时 ${minutes}分 ${remain}秒` : `${minutes}分 ${remain}秒`;
  }

  function formatDate(value) {
    return value ? new Date(value).toLocaleString() : '—';
  }

  function scoreClass(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    return number >= 85 ? 'good' : number >= 65 ? 'warn' : 'bad';
  }

  function markup() {
    return `
      <header class="ops-page-heading">
        <div><span class="page-kicker">LIVE SESSION OBSERVABILITY</span><h2>直播记录与质量诊断</h2>
        <p>完整记录观众识别、导演决策、命令状态、ChatGPT 回答、Bridge/扩展心跳和配置快照。结束后导出 ZIP，便于继续分析整场直播效果。</p></div>
        <div class="actions"><button id="live-run-refresh" type="button" class="secondary">刷新</button></div>
      </header>
      <section class="ops-grid">
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">CURRENT RUN</span><h2>当前直播记录</h2></div>
          <span id="live-run-badge" class="badge warn">读取中</span></div>
          <div id="live-run-stats" class="ops-stat-grid"></div>
          <label>本场标题<input id="live-run-title" placeholder="例如：AI 日常聊天直播"></label>
          <label class="check-row"><input id="live-run-include-text" type="checkbox" checked>在诊断包中保留观众文本与 ChatGPT 回答</label>
          <p class="hint">诊断包会自动移除管理令牌、API Key 和供应商密钥；保留观众文本有助于分析识别和回复质量，请自行妥善保存。</p>
          <div class="ops-toolbar">
            <button id="live-run-start" type="button">开始记录</button>
            <button id="live-run-snapshot" type="button" class="secondary">立即采样</button>
            <button id="live-run-export" type="button" class="secondary">导出当前 ZIP</button>
            <button id="live-run-stop" type="button" class="danger">结束并导出</button>
          </div>
          <div id="live-run-message" class="diagnosis warn">正在读取状态。</div>
          <code id="live-run-path" class="ops-path"></code>
        </article>
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">AUTOMATION</span><h2>自动记录设置</h2></div></div>
          <label class="check-row"><input id="live-run-auto" type="checkbox">自动导演开始执导时自动记录</label>
          <label class="check-row"><input id="live-run-default-text" type="checkbox" checked>新直播默认保留互动文本</label>
          <div class="inline-fields">
            <label>事件采样间隔（秒）<input id="live-run-sample" type="number" min="1" max="30" step="1" value="2"></label>
            <label>状态指标间隔（秒）<input id="live-run-metric" type="number" min="2" max="60" step="1" value="5"></label>
          </div>
          <button id="live-run-save-settings" type="button">保存自动记录设置</button>
          <div class="voice-notice">建议正式开播前开启自动记录。停止自动导演时，系统会自动结束记录并生成质量摘要和 ZIP。</div>
        </article>
      </section>
      <article class="panel" style="margin-top:12px">
        <div class="section-title"><div><span class="page-kicker">HISTORY</span><h2>历史直播记录</h2></div>
        <span class="hint">质量分只用于发现链路异常，不代表内容好坏。</span></div>
        <div id="live-run-history"><p class="hint">尚未读取。</p></div>
      </article>`;
  }

  function mount() {
    const root = document.getElementById('live-run-console-root');
    if (!root || state.mounted) return Boolean(root);
    state.mounted = true;
    root.innerHTML = markup();
    document.getElementById('live-run-refresh').addEventListener('click', () => refresh(true));
    document.getElementById('live-run-start').addEventListener('click', startRun);
    document.getElementById('live-run-snapshot').addEventListener('click', snapshot);
    document.getElementById('live-run-export').addEventListener('click', exportRun);
    document.getElementById('live-run-stop').addEventListener('click', stopRun);
    document.getElementById('live-run-save-settings').addEventListener('click', saveSettings);
    return true;
  }

  function renderStatus() {
    const value = state.status || {};
    const active = Boolean(value.active);
    const badge = document.getElementById('live-run-badge');
    badge.textContent = active ? '正在记录' : '待机';
    badge.className = `badge ${active ? 'good' : 'warn'}`;
    document.getElementById('live-run-stats').innerHTML = `
      <div class="ops-stat"><strong>${formatDuration(value.duration_seconds)}</strong><span>持续时间</span></div>
      <div class="ops-stat"><strong>${value.record_count || 0}</strong><span>时间线记录</span></div>
      <div class="ops-stat"><strong>${value.last_sample_at ? '正常' : '等待'}</strong><span>采样状态</span></div>
      <div class="ops-stat"><strong>${value.include_audience_text ? '完整' : '隐私模式'}</strong><span>文本记录</span></div>`;
    document.getElementById('live-run-start').disabled = active;
    document.getElementById('live-run-stop').disabled = !active;
    document.getElementById('live-run-snapshot').disabled = !active;
    document.getElementById('live-run-export').disabled = !active && !value.bundle_path;
    document.getElementById('live-run-title').disabled = active;
    document.getElementById('live-run-include-text').disabled = active;
    const message = document.getElementById('live-run-message');
    if (value.last_error) {
      message.className = 'diagnosis bad';
      message.textContent = value.last_error;
    } else if (active) {
      message.className = 'diagnosis good';
      message.textContent = `正在记录：${value.title || value.run_id}。每次观众识别、导演决策和命令状态变化都会写入时间线。`;
    } else {
      message.className = 'diagnosis warn';
      message.textContent = '当前没有正在记录的直播。可手动开始，或启用自动导演联动。';
    }
    document.getElementById('live-run-path').textContent = value.bundle_path || value.path || '';
    const settings = value.settings || {};
    document.getElementById('live-run-auto').checked = Boolean(settings.auto_start_on_director);
    document.getElementById('live-run-default-text').checked = settings.include_audience_text !== false;
    document.getElementById('live-run-sample').value = settings.sample_interval_seconds || 2;
    document.getElementById('live-run-metric').value = settings.metric_interval_seconds || 5;
  }

  function renderHistory() {
    const host = document.getElementById('live-run-history');
    if (!state.runs.length) {
      host.innerHTML = '<p class="hint">还没有历史直播记录。</p>';
      return;
    }
    host.innerHTML = `<table class="ops-table"><thead><tr><th>直播</th><th>时间</th><th>质量</th><th>互动 / 命令</th><th>操作</th></tr></thead><tbody>${state.runs.map(row => `
      <tr><td><strong>${escapeHtml(row.title || row.run_id)}</strong><div class="meta">${escapeHtml(row.source || '')}</div></td>
      <td>${formatDate(row.started_at)}<div class="meta">${formatDuration(row.duration_seconds)}</div></td>
      <td><span class="ops-score ${scoreClass(row.quality_score)}">${row.quality_score ?? '—'}</span></td>
      <td>${row.events ?? '—'} / ${row.commands ?? '—'}</td>
      <td><div class="actions"><button type="button" class="secondary" data-download-run="${escapeHtml(row.run_id)}">下载 ZIP</button>
      <button type="button" class="danger" data-delete-run="${escapeHtml(row.run_id)}">删除</button></div></td></tr>`).join('')}</tbody></table>`;
    host.querySelectorAll('[data-download-run]').forEach(button => {
      button.addEventListener('click', () => downloadRun(button.dataset.downloadRun));
    });
    host.querySelectorAll('[data-delete-run]').forEach(button => {
      button.addEventListener('click', () => deleteRun(button.dataset.deleteRun));
    });
  }

  async function refresh(showToast = false) {
    if (state.busy || !mount()) return;
    state.busy = true;
    try {
      [state.status, state.runs] = await Promise.all([
        api('/api/live-runs/status'),
        api('/api/live-runs?limit=50'),
      ]);
      renderStatus();
      renderHistory();
      if (showToast) toast('直播记录状态已刷新');
    } catch (error) {
      if (showToast) toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function startRun() {
    try {
      state.status = await api('/api/live-runs/start', {
        method: 'POST',
        body: JSON.stringify({
          title: document.getElementById('live-run-title').value.trim() || null,
          include_audience_text: document.getElementById('live-run-include-text').checked,
        }),
      });
      renderStatus();
      toast('直播运行记录已开始');
    } catch (error) { toast(error.message, true); }
  }

  async function snapshot() {
    try {
      state.status = await api('/api/live-runs/snapshot', { method: 'POST' });
      renderStatus();
      toast('已写入一次即时状态采样');
    } catch (error) { toast(error.message, true); }
  }

  async function exportRun() {
    try {
      state.status = await api('/api/live-runs/export', { method: 'POST' });
      renderStatus();
      toast('当前诊断 ZIP 已更新');
      if (state.status.run_id) await downloadRun(state.status.run_id);
    } catch (error) { toast(error.message, true); }
  }

  async function stopRun() {
    try {
      state.status = await api('/api/live-runs/stop', { method: 'POST' });
      renderStatus();
      toast(`直播记录已结束，质量分 ${state.status.quality_summary?.quality_score ?? '—'}`);
      if (state.status.run_id) await downloadRun(state.status.run_id);
      await refresh();
    } catch (error) { toast(error.message, true); }
  }

  async function saveSettings() {
    try {
      const settings = await api('/api/live-runs/settings', {
        method: 'PUT',
        body: JSON.stringify({
          auto_start_on_director: document.getElementById('live-run-auto').checked,
          include_audience_text: document.getElementById('live-run-default-text').checked,
          sample_interval_seconds: Number(document.getElementById('live-run-sample').value || 2),
          metric_interval_seconds: Number(document.getElementById('live-run-metric').value || 5),
        }),
      });
      state.status = { ...(state.status || {}), settings };
      renderStatus();
      toast('自动记录设置已保存');
    } catch (error) { toast(error.message, true); }
  }

  async function downloadRun(runId) {
    try {
      const response = await fetch(`/api/live-runs/${encodeURIComponent(runId)}/download`, { headers: headers() });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `aliver-live-run-${runId}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { toast(error.message, true); }
  }

  async function deleteRun(runId) {
    if (!confirm('确定删除这份直播记录和 ZIP 吗？')) return;
    try {
      await api(`/api/live-runs/${encodeURIComponent(runId)}`, { method: 'DELETE' });
      await refresh();
      toast('直播记录已删除');
    } catch (error) { toast(error.message, true); }
  }

  function start() {
    if (!mount()) {
      const observer = new MutationObserver(() => {
        if (mount()) observer.disconnect();
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    setTimeout(() => refresh(), 1000);
    setInterval(() => refresh(), 4000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
