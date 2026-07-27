const autoDirectorState = {
  extensions: [],
  config: null,
  status: null,
  events: [],
  loading: false,
};

function selectedAutoDirectorExtension() {
  return document.getElementById('auto-director-extension')?.value || '';
}

function linesFrom(value) {
  return String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean);
}

function renderAutoDirectorExtensions() {
  const select = document.getElementById('auto-director-extension');
  const previous = select.value;
  select.innerHTML = '<option value="">请选择导演扩展</option>' + autoDirectorState.extensions.map(extension =>
    `<option value="${extension.id}">${escapeHtml(extension.name)}（${extension.connected ? '在线' : '离线'}）</option>`
  ).join('');
  if (autoDirectorState.extensions.some(extension => extension.id === previous)) select.value = previous;
  else if (autoDirectorState.extensions.length === 1) select.value = autoDirectorState.extensions[0].id;
}

function setAutoDirectorModeVisibility() {
  const mode = document.getElementById('auto-director-mode').value;
  document.getElementById('auto-director-ai-fields').classList.toggle('visible', mode === 'openai_compatible');
}

function fillAutoDirectorConfig(config) {
  const form = document.getElementById('auto-director-config-form');
  const settings = config?.settings || {};
  form.elements.enabled.checked = Boolean(config?.enabled);
  form.elements.mode.value = config?.mode || 'rules';
  form.elements.api_base_url.value = config?.api_base_url || '';
  form.elements.model_name.value = config?.model_name || '';
  form.elements.api_key.value = '';
  form.elements.api_key.placeholder = config?.credential_keys?.includes('api_key')
    ? '已保存 API Key；留空表示保留'
    : '尚未保存 API Key';
  form.elements.min_score.value = settings.min_score ?? 35;
  form.elements.cooldown_seconds.value = settings.cooldown_seconds ?? 12;
  form.elements.idle_seconds.value = settings.idle_seconds ?? 120;
  form.elements.max_response_seconds.value = settings.max_response_seconds ?? 25;
  form.elements.dedupe_window_seconds.value = settings.dedupe_window_seconds ?? 90;
  form.elements.blocked_keywords.value = (settings.blocked_keywords || []).join('\n');
  form.elements.idle_topics.value = (settings.idle_topics || []).join('\n');
  setAutoDirectorModeVisibility();
}

function renderAutoDirectorStatus() {
  const badge = document.getElementById('auto-director-badge');
  const container = document.getElementById('auto-director-status');
  const status = autoDirectorState.status;
  if (!status) {
    badge.className = 'badge warn';
    badge.textContent = '未配置';
    container.innerHTML = '<p class="hint">请选择并保存目标 Chrome 扩展。</p>';
    return;
  }
  badge.className = `badge ${status.enabled ? 'ok' : 'warn'}`;
  badge.textContent = status.enabled ? '运行中' : '已停用';
  container.innerHTML = `
    <div class="auto-status-item"><span>扩展</span><strong>${status.extension_connected ? '在线' : '离线'}</strong></div>
    <div class="auto-status-item"><span>ChatGPT</span><strong>${status.chatgpt_open ? '已打开' : '未检测到'}</strong></div>
    <div class="auto-status-item"><span>输入框</span><strong>${status.composer_ready ? '就绪' : '未就绪'}</strong></div>
    <div class="auto-status-item"><span>回答状态</span><strong>${status.generating ? '正在回答' : '空闲'}</strong></div>
    <div class="auto-status-item"><span>待处理事件</span><strong>${status.queued_events}</strong></div>
    <div class="auto-status-item"><span>已选事件</span><strong>${status.selected_events}</strong></div>
    <div class="auto-status-item"><span>已过滤事件</span><strong>${status.ignored_events}</strong></div>
    <div class="auto-status-item"><span>待完成命令</span><strong>${status.pending_commands}</strong></div>
    <div class="auto-status-item"><span>最后发送</span><strong>${formatTime(status.last_dispatched_at)}</strong></div>
  `;
}

function eventStatus(value) {
  return `<span class="director-status ${escapeHtml(value)}">${escapeHtml(value)}</span>`;
}

function renderAutoDirectorEvents() {
  const container = document.getElementById('auto-director-event-list');
  container.innerHTML = autoDirectorState.events.map(event => `
    <div class="auto-event-card ${escapeHtml(event.status)}">
      <div class="item-head">
        <div>
          <strong>${escapeHtml(event.event_type)} · ${escapeHtml(event.user_name || '观众')}</strong>
          <div class="meta">${escapeHtml(event.platform)} · ${formatTime(event.created_at)} · 评分 ${event.score}</div>
        </div>
        ${eventStatus(event.status)}
      </div>
      <div class="auto-event-content">${escapeHtml(event.content || '（无文字内容）')}</div>
      <div class="meta">${escapeHtml(event.reason || '')}</div>
      ${event.selected_command_id ? `<div class="meta">命令：${escapeHtml(event.selected_command_id)}</div>` : ''}
      <div class="actions">
        <button class="secondary" onclick="retryAutoDirectorEvent('${event.id}')" ${event.status === 'queued' ? 'disabled' : ''}>重新排队</button>
      </div>
    </div>
  `).join('') || '<p class="hint">暂无互动事件。</p>';
}

async function loadAutoDirector() {
  if (autoDirectorState.loading) return;
  autoDirectorState.loading = true;
  try {
    autoDirectorState.extensions = await api('/api/director/extensions');
    renderAutoDirectorExtensions();
    const extensionId = selectedAutoDirectorExtension();
    if (!extensionId) {
      autoDirectorState.config = null;
      autoDirectorState.status = null;
      autoDirectorState.events = [];
      renderAutoDirectorStatus();
      renderAutoDirectorEvents();
      return;
    }
    const [config, status, events] = await Promise.all([
      api(`/api/auto-director/config?extension_id=${encodeURIComponent(extensionId)}`),
      api(`/api/auto-director/status?extension_id=${encodeURIComponent(extensionId)}`),
      api(`/api/auto-director/events?extension_id=${encodeURIComponent(extensionId)}&limit=100`),
    ]);
    autoDirectorState.config = config;
    autoDirectorState.status = status;
    autoDirectorState.events = events;
    fillAutoDirectorConfig(config);
    renderAutoDirectorStatus();
    renderAutoDirectorEvents();
  } finally {
    autoDirectorState.loading = false;
  }
}

async function saveAutoDirectorConfig(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const extensionId = form.get('extension_id');
  if (!extensionId) throw new Error('请先选择 Chrome 导演扩展');
  const payload = {
    extension_id: extensionId,
    enabled: form.get('enabled') === 'on',
    mode: form.get('mode'),
    api_base_url: form.get('api_base_url') || null,
    model_name: form.get('model_name') || null,
    api_key: form.get('api_key') || null,
    settings: {
      min_score: Number(form.get('min_score') || 35),
      cooldown_seconds: Number(form.get('cooldown_seconds') || 12),
      idle_seconds: Number(form.get('idle_seconds') || 120),
      max_response_seconds: Number(form.get('max_response_seconds') || 25),
      dedupe_window_seconds: Number(form.get('dedupe_window_seconds') || 90),
      max_comment_chars: 300,
      temperature: 0.3,
      blocked_keywords: linesFrom(form.get('blocked_keywords')),
      idle_topics: linesFrom(form.get('idle_topics')),
    },
  };
  autoDirectorState.config = await api('/api/auto-director/config', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
  toast(payload.enabled ? '自动导演配置已保存并启用' : '自动导演配置已保存');
  await loadAutoDirector();
}

async function submitAutoDirectorEvent(event) {
  event.preventDefault();
  const extensionId = selectedAutoDirectorExtension();
  if (!extensionId) throw new Error('请先选择 Chrome 导演扩展');
  const form = new FormData(event.target);
  const result = await api('/api/auto-director/events', {
    method: 'POST',
    body: JSON.stringify({
      extension_id: extensionId,
      event_type: form.get('event_type'),
      platform: form.get('platform') || 'manual',
      user_name: form.get('user_name') || '观众',
      content: form.get('content') || '',
      payload: {},
    }),
  });
  const diagnosis = document.getElementById('auto-director-last-result');
  diagnosis.className = `diagnosis ${result.status === 'ignored' ? 'bad' : 'ok'}`;
  diagnosis.textContent = `事件已进入 ${result.status}，评分 ${result.score}：${result.reason || ''}`;
  if (result.status !== 'ignored') event.target.elements.content.value = '';
  await loadAutoDirector();
}

async function processAutoDirectorOnce() {
  const extensionId = selectedAutoDirectorExtension();
  if (!extensionId) throw new Error('请先选择 Chrome 导演扩展');
  const result = await api(`/api/auto-director/process?extension_id=${encodeURIComponent(extensionId)}&force=true`, {
    method: 'POST',
  });
  toast(result.processed ? `已处理：${result.action || '完成'}` : result.reason, !result.processed);
  await loadAutoDirector();
  if (typeof loadDirector === 'function') await loadDirector();
}

window.retryAutoDirectorEvent = async id => {
  try {
    await api(`/api/auto-director/events/${id}/retry`, { method: 'POST' });
    toast('事件已重新排队');
    await loadAutoDirector();
  } catch (error) { toast(error.message, true); }
};

document.getElementById('auto-director-config-form').addEventListener('submit', event => {
  saveAutoDirectorConfig(event).catch(error => toast(error.message, true));
});
document.getElementById('auto-director-event-form').addEventListener('submit', event => {
  submitAutoDirectorEvent(event).catch(error => toast(error.message, true));
});
document.getElementById('auto-director-extension').addEventListener('change', () => {
  autoDirectorState.config = null;
  loadAutoDirector().catch(error => toast(error.message, true));
});
document.getElementById('auto-director-mode').addEventListener('change', setAutoDirectorModeVisibility);
document.getElementById('auto-director-refresh').addEventListener('click', () => {
  loadAutoDirector().catch(error => toast(error.message, true));
});
document.getElementById('auto-director-process').addEventListener('click', () => {
  processAutoDirectorOnce().catch(error => toast(error.message, true));
});

loadAutoDirector().catch(() => {});
setInterval(() => {
  const panel = document.getElementById('tab-auto-director');
  if (panel?.classList.contains('active')) loadAutoDirector().catch(() => {});
}, 4000);
