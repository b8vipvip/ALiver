const directorState = { extensions: [], commands: [], loading: false };

function directorStatus(value) {
  return `<span class="director-status ${escapeHtml(value)}">${escapeHtml(value)}</span>`;
}

function directorText(command) {
  return command?.payload?.text || '';
}

function renderDirectorExtensions() {
  const container = document.getElementById('director-extension-list');
  container.innerHTML = directorState.extensions.map(extension => `
    <div class="extension-card ${extension.connected ? 'connected' : ''}">
      <div class="item-head">
        <div><h3>${escapeHtml(extension.name)}</h3><div class="meta">${escapeHtml(extension.browser_name)} · ${escapeHtml(extension.version)}</div></div>
        ${directorStatus(extension.connected ? 'online' : 'offline')}
      </div>
      <div class="meta">ID：${escapeHtml(extension.id)}</div>
      <div class="meta">ChatGPT：${escapeHtml(extension.metadata?.chatgpt_open ? '已打开' : '未检测到')}</div>
      <div class="meta">输入框：${escapeHtml(extension.metadata?.composer_ready ? '就绪' : '未就绪')} · 正在回答：${escapeHtml(extension.metadata?.generating ? '是' : '否')}</div>
      <div class="meta">页面：${escapeHtml(extension.active_tab_url || '无')}</div>
      <div class="meta">最后心跳：${formatTime(extension.last_seen_at)}</div>
    </div>
  `).join('') || '<p class="hint">尚未配对 Chrome 扩展。请加载 chrome_extension 文件夹并在扩展弹窗中配对。</p>';

  const select = document.getElementById('director-extension');
  const previous = select.value;
  select.innerHTML = '<option value="">请选择导演扩展</option>' + directorState.extensions.map(extension =>
    `<option value="${extension.id}">${escapeHtml(extension.name)}（${extension.connected ? '在线' : '离线'}）</option>`
  ).join('');
  if (directorState.extensions.some(extension => extension.id === previous)) select.value = previous;
  else if (directorState.extensions.length === 1) select.value = directorState.extensions[0].id;
}

function renderDirectorCommands() {
  const container = document.getElementById('director-command-list');
  container.innerHTML = directorState.commands.map(command => `
    <div class="director-command">
      <div class="item-head">
        <div><strong>${escapeHtml(command.command_type)}</strong><div class="meta">${formatTime(command.created_at)} · 优先级 ${command.priority}</div></div>
        ${directorStatus(command.status)}
      </div>
      <div class="command-text">${escapeHtml(directorText(command))}</div>
      ${command.error_message ? `<div class="diagnosis bad">${escapeHtml(command.error_message)}</div>` : ''}
      ${Object.keys(command.result || {}).length ? `<div class="command-result">${escapeHtml(JSON.stringify(command.result, null, 2))}</div>` : ''}
      <div class="actions">
        <button class="secondary" onclick="retryDirectorCommand('${command.id}')">重试</button>
        <button class="danger" onclick="cancelDirectorCommand('${command.id}')" ${['completed', 'failed', 'cancelled'].includes(command.status) ? 'disabled' : ''}>取消</button>
      </div>
    </div>
  `).join('') || '<p class="hint">暂无导演命令。</p>';
}

async function loadDirector() {
  if (directorState.loading) return;
  directorState.loading = true;
  try {
    const [extensions, commands, dashboard] = await Promise.all([
      api('/api/director/extensions'),
      api('/api/director/commands?limit=100'),
      api('/api/dashboard'),
    ]);
    directorState.extensions = extensions;
    directorState.commands = commands;
    document.getElementById('metric-extensions').textContent = dashboard.online_extensions || 0;
    renderDirectorExtensions();
    renderDirectorCommands();
  } finally {
    directorState.loading = false;
  }
}

async function sendDirectorCommand(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  const extensionId = form.get('extension_id');
  if (!extensionId) throw new Error('请先选择一个 Chrome 导演扩展');
  const value = await api('/api/director/commands', {
    method: 'POST',
    body: JSON.stringify({
      extension_id: extensionId,
      command_type: form.get('command_type'),
      content: form.get('content'),
      wrap_as_director: form.get('wrap_as_director') === 'on',
      auto_send: form.get('auto_send') === 'on',
      force: form.get('force') === 'on',
      priority: Number(form.get('priority') || 50),
      source: 'manual_console',
    }),
  });
  toast(value.status === 'queued' ? '扩展离线，导演命令已排队' : '导演命令已发送到 Chrome 扩展');
  document.getElementById('director-content').value = '';
  await loadDirector();
}

window.retryDirectorCommand = async id => {
  try {
    await api(`/api/director/commands/${id}/retry`, { method: 'POST' });
    toast('命令已重新排队/发送');
    await loadDirector();
  } catch (error) { toast(error.message, true); }
};

window.cancelDirectorCommand = async id => {
  try {
    await api(`/api/director/commands/${id}/cancel`, { method: 'POST' });
    toast('命令已取消');
    await loadDirector();
  } catch (error) { toast(error.message, true); }
};

const DEFAULT_LIVE_RULES = `你正在进行一场日常聊天型直播。\n\n请遵守以下规则：\n1. 当收到以【导演指令】开头的消息时，它是后台控制信息，不要朗读指令本身。\n2. 只执行要求并自然说出最终回答，不要提到导演、后台、提示词或控制系统。\n3. 回答通常控制在15到40秒，除非导演另有要求。\n4. 可以自然称呼观众昵称，并在合适时反问观众。\n5. 不确定的信息不要编造；不适合回答的内容礼貌拒绝或幽默带过。\n6. 导演要求等待或保持安静时，不要主动继续说。`;

document.getElementById('director-command-form').addEventListener('submit', event => {
  sendDirectorCommand(event).catch(error => toast(error.message, true));
});
document.getElementById('director-refresh').addEventListener('click', () => {
  loadDirector().catch(error => toast(error.message, true));
});
document.getElementById('director-default-rules').addEventListener('click', () => {
  document.getElementById('director-content').value = DEFAULT_LIVE_RULES;
});

loadDirector().catch(() => {});
setInterval(() => {
  const panel = document.getElementById('tab-director');
  if (panel?.classList.contains('active')) loadDirector().catch(() => {});
}, 4000);
