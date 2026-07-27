(() => {
  const statusText = {
    starting: '启动中',
    active: '运行中',
    running: '运行中',
    ready: '就绪',
    failed: '启动失败',
    ended: '已结束',
    ended_local_only: '仅本地结束',
    stop_failed: '停止失败',
    awaiting_manual: '等待人工操作',
    online: '在线',
    offline: '离线',
    disabled: '已禁用',
  };

  const categoryText = {
    'session.start': '会话启动',
    'session.stop': '会话停止',
    'provider.test': '供应商连接测试',
    'provider.created': '新增供应商',
    'provider.updated': '更新供应商',
    'bridge.connected': 'Bridge 已连接',
    'bridge.disconnected': 'Bridge 已断开',
    'bridge.command': 'Bridge 命令',
    'director.extension.connected': '导演扩展已连接',
    'director.extension.disconnected': '导演扩展已断开',
    'director.command.result': '导演命令结果',
  };

  function badgeZh(value) {
    const good = ['active', 'running', 'ready', 'online', 'ended'];
    const warn = ['starting', 'awaiting_manual', 'offline', 'ended_local_only'];
    const cls = good.includes(value) ? 'good' : warn.includes(value) ? 'warn' : 'bad';
    return `<span class="badge ${cls}">${escapeHtml(statusText[value] || value)}</span>`;
  }

  function pretty(value) {
    return escapeHtml(JSON.stringify(value ?? {}, null, 2));
  }

  function hasDetails(value) {
    return value && typeof value === 'object' && Object.keys(value).length > 0;
  }

  loadSessions = async function loadSessionsZh() {
    state.sessions = await api('/api/sessions');
    document.getElementById('session-list').innerHTML = state.sessions.map(s => {
      const response = s.response || {};
      const bridgeError = response.error_detail || response.data?.error_detail || null;
      const diagnosis = bridgeError || response;
      const error = s.error_message || bridgeError?.message_zh || '';
      return `
        <div class="item">
          <div class="item-head">
            <div>
              <h3>${escapeHtml(s.provider_name || s.provider_config_id)}</h3>
              <div class="meta">${escapeHtml(s.provider_type || '')} · ${escapeHtml(s.id)}</div>
            </div>
            ${badgeZh(s.status)}
          </div>
          <div class="meta">外部会话：${escapeHtml(s.external_session_id || '无')} · Bridge：${escapeHtml(s.bridge_id || '无')}</div>
          ${error ? `<div class="diagnosis bad"><strong>异常摘要</strong><pre>${escapeHtml(error)}</pre></div>` : ''}
          ${hasDetails(diagnosis) ? `
            <details>
              <summary>查看完整会话诊断</summary>
              <pre>${pretty(diagnosis)}</pre>
            </details>` : ''}
          <div class="actions">
            <button class="danger" ${['ended', 'ended_local_only'].includes(s.status) ? 'disabled' : ''} onclick="stopSession('${s.id}')">停止</button>
          </div>
        </div>`;
    }).join('') || '<p class="hint">暂无会话。</p>';
  };

  loadLogs = async function loadLogsZh() {
    const [logs, summary] = await Promise.all([
      api('/api/logs?limit=150'),
      api('/api/logs/summary'),
    ]);
    document.getElementById('log-summary').textContent =
      `日志级别：${JSON.stringify(summary.levels)} · 延迟统计：${JSON.stringify(summary.latency_ms)}`;
    document.getElementById('log-list').innerHTML = logs.map(row => {
      const levelText = row.level === 'ERROR' ? '错误' : row.level === 'WARN' ? '警告' : '信息';
      const category = categoryText[row.category] || row.category;
      return `
        <div class="item">
          <div class="item-head">
            <div>
              <strong>${escapeHtml(category)}</strong>
              <div class="meta">${formatTime(row.created_at)}${row.latency_ms == null ? '' : ` · ${row.latency_ms} ms`}</div>
            </div>
            <span class="badge ${row.level === 'ERROR' ? 'bad' : row.level === 'WARN' ? 'warn' : 'good'}">${levelText}</span>
          </div>
          <div>${escapeHtml(row.message)}</div>
          ${hasDetails(row.details) ? `
            <details>
              <summary>查看详细上下文</summary>
              <pre>${pretty(row.details)}</pre>
            </details>` : ''}
        </div>`;
    }).join('') || '<p class="hint">暂无日志。</p>';
  };

  setTimeout(() => {
    loadSessions().catch(error => toast(error.message, true));
    loadLogs().catch(error => toast(error.message, true));
  }, 0);
})();
