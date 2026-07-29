(() => {
  const SESSION_NAME_KEY = '_session_name';
  const ACTIVE_SESSION_STATUSES = new Set([
    'starting', 'active', 'running', 'ready', 'awaiting_manual', 'reconnecting',
  ]);
  const ACTION_LABELS = {
    idle: '待机',
    talking: '说话',
    thinking: '思考',
    wave: '挥手',
    happy: '开心',
    surprised: '惊讶',
    reset: '恢复',
  };

  let providerEditId = '';
  let sessionEditId = '';
  let directorEditId = '';
  let mappingDirty = false;

  function randomSessionName() {
    const now = new Date();
    const stamp = [
      String(now.getMonth() + 1).padStart(2, '0'),
      String(now.getDate()).padStart(2, '0'),
      '-',
      String(now.getHours()).padStart(2, '0'),
      String(now.getMinutes()).padStart(2, '0'),
    ].join('');
    return `直播会话-${stamp}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
  }

  function sessionName(session) {
    return session?.request?.[SESSION_NAME_KEY]
      || `${session?.provider_name || '数字人会话'}-${String(session?.id || '').slice(0, 8)}`;
  }

  function sessionOverrides(session) {
    const value = { ...(session?.request || {}) };
    delete value[SESSION_NAME_KEY];
    return value;
  }

  function providerForm() {
    return document.getElementById('provider-form');
  }

  function sessionForm() {
    return document.getElementById('session-form');
  }

  function resetProviderEditor() {
    const form = providerForm();
    providerEditId = '';
    form.reset();
    form.querySelector('[name="provider_type"]').disabled = false;
    form.querySelector('[name="credentials"]').placeholder = '{"api_key":"..."}';
    document.getElementById('provider-form-title').textContent = '新增数字人供应商';
    document.getElementById('provider-save-button').textContent = '保存供应商';
    document.getElementById('provider-edit-cancel').hidden = true;
  }

  function resetSessionEditor() {
    const form = sessionForm();
    sessionEditId = '';
    form.reset();
    form.querySelector('[name="name"]').value = randomSessionName();
    form.querySelector('[name="provider_config_id"]').disabled = false;
    form.querySelector('[name="bridge_id"]').disabled = false;
    form.querySelector('[name="overrides"]').disabled = false;
    document.getElementById('session-form-title').textContent = '启动数字人会话';
    document.getElementById('session-save-button').textContent = '启动会话';
    document.getElementById('session-edit-cancel').hidden = true;
  }

  function resetDirectorEditor() {
    directorEditId = '';
    const form = document.getElementById('director-command-form');
    document.getElementById('director-editor-title').textContent = '人工导演指令';
    form.querySelector('button[type="submit"]').textContent = '发送到 ChatGPT';
    document.getElementById('director-edit-cancel').hidden = true;
  }

  async function managedLoadProviders() {
    const previous = document.getElementById('session-provider')?.value || '';
    state.providers = await api('/api/providers');
    const list = document.getElementById('provider-list');
    list.innerHTML = state.providers.map(provider => `
      <div class="item">
        <div class="item-head">
          <div>
            <h3>${escapeHtml(provider.name)}</h3>
            <div class="meta">${escapeHtml(provider.provider_type)} · ${escapeHtml(provider.execution_mode)} · ${escapeHtml(provider.id)}</div>
          </div>
          ${statusBadge(provider.enabled ? 'active' : 'disabled')}
        </div>
        <div class="meta">密钥字段：${provider.credential_keys.join(', ') || '无'} · 更新：${formatTime(provider.updated_at)}</div>
        <pre>${escapeHtml(JSON.stringify(provider.settings, null, 2))}</pre>
        <div class="actions">
          <button onclick="testProvider('${provider.id}')">测试连接</button>
          <button class="secondary" onclick="editProvider('${provider.id}')">修改</button>
          <button class="secondary" onclick="toggleProvider('${provider.id}', ${!provider.enabled})">${provider.enabled ? '禁用' : '启用'}</button>
          <button class="danger" onclick="deleteProvider('${provider.id}')">删除</button>
        </div>
      </div>
    `).join('') || '<p class="hint">暂无供应商。</p>';

    const select = document.getElementById('session-provider');
    select.innerHTML = state.providers
      .filter(provider => provider.enabled)
      .map(provider => `<option value="${provider.id}">${escapeHtml(provider.name)}（${escapeHtml(provider.provider_type)}）</option>`)
      .join('');
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
  }

  async function managedLoadSessions() {
    state.sessions = await api('/api/sessions');
    document.getElementById('session-list').innerHTML = state.sessions.map(session => {
      const active = ACTIVE_SESSION_STATUSES.has(session.status);
      const deletable = !active && session.status !== 'stop_failed';
      return `
        <div class="item">
          <div class="item-head">
            <div>
              <h3>${escapeHtml(sessionName(session))}</h3>
              <div class="meta">${escapeHtml(session.provider_name || session.provider_config_id)} · ${escapeHtml(session.provider_type || '')}</div>
            </div>
            ${statusBadge(session.status)}
          </div>
          <div class="meta">Session ID：${escapeHtml(session.id)}</div>
          <div class="meta">外部会话：${escapeHtml(session.external_session_id || '无')} · Bridge：${escapeHtml(session.bridge_id || '无')}</div>
          <div class="meta">创建：${formatTime(session.created_at)} · 更新：${formatTime(session.updated_at)}</div>
          ${session.error_message ? `<pre>${escapeHtml(session.error_message)}</pre>` : ''}
          <div class="actions">
            <button class="secondary" onclick="editSession('${session.id}')">修改</button>
            <button onclick="restartSession('${session.id}')" ${active ? 'disabled' : ''}>重新启用</button>
            <button class="danger" onclick="stopSession('${session.id}')" ${active || session.status === 'stop_failed' ? '' : 'disabled'}>停止</button>
            <button class="danger" onclick="deleteSession('${session.id}')" ${deletable ? '' : 'disabled'}>删除</button>
          </div>
        </div>
      `;
    }).join('') || '<p class="hint">暂无会话。</p>';
  }

  function commandRawContent(command) {
    const content = command?.payload?.content;
    if (content) return content;
    const text = command?.payload?.text || '';
    const marker = '\n\n';
    return text.startsWith('【导演指令】') && text.includes(marker)
      ? text.slice(text.indexOf(marker) + marker.length)
      : text;
  }

  function managedRenderDirectorCommands() {
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
          <button class="secondary" onclick="editDirectorCommand('${command.id}')" ${command.status === 'dispatched' ? 'disabled' : ''}>编辑重发</button>
          <button class="secondary" onclick="retryDirectorCommand('${command.id}')">重试</button>
          <button class="danger" onclick="cancelDirectorCommand('${command.id}')" ${['completed', 'failed', 'cancelled'].includes(command.status) ? 'disabled' : ''}>取消</button>
          <button class="danger" onclick="deleteDirectorCommand('${command.id}')" ${command.status === 'dispatched' ? 'disabled' : ''}>删除</button>
        </div>
      </div>
    `).join('') || '<p class="hint">暂无导演命令。</p>';
  }

  async function managedSendDirectorCommand(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    const extensionId = form.get('extension_id');
    if (!extensionId) throw new Error('请先选择一个 Chrome 导演扩展');
    const payload = {
      extension_id: extensionId,
      command_type: form.get('command_type'),
      content: form.get('content'),
      wrap_as_director: form.get('wrap_as_director') === 'on',
      auto_send: form.get('auto_send') === 'on',
      force: form.get('force') === 'on',
      priority: Number(form.get('priority') || 50),
      source: 'manual_console',
    };
    const path = directorEditId
      ? `/api/director/commands/${directorEditId}`
      : '/api/director/commands';
    const value = await api(path, {
      method: directorEditId ? 'PATCH' : 'POST',
      body: JSON.stringify(payload),
    });
    toast(directorEditId
      ? '导演命令已修改并重新排队/发送'
      : value.status === 'queued'
        ? '扩展离线，导演命令已排队'
        : '导演命令已发送到 Chrome 扩展');
    document.getElementById('director-content').value = '';
    resetDirectorEditor();
    await loadDirector();
  }

  window.editProvider = id => {
    const provider = state.providers.find(row => row.id === id);
    if (!provider) return toast('未找到供应商', true);
    providerEditId = id;
    const form = providerForm();
    form.querySelector('[name="name"]').value = provider.name;
    form.querySelector('[name="provider_type"]').value = provider.provider_type;
    form.querySelector('[name="provider_type"]').disabled = true;
    form.querySelector('[name="api_base_url"]').value = provider.api_base_url || '';
    form.querySelector('[name="credentials"]').value = '';
    form.querySelector('[name="credentials"]').placeholder = '留空则保留原密钥；输入 {} 可清空';
    form.querySelector('[name="settings"]').value = JSON.stringify(provider.settings || {}, null, 2);
    document.getElementById('provider-form-title').textContent = `修改供应商：${provider.name}`;
    document.getElementById('provider-save-button').textContent = '保存修改';
    document.getElementById('provider-edit-cancel').hidden = false;
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  window.deleteProvider = async id => {
    const provider = state.providers.find(row => row.id === id);
    if (!provider) return;
    if (!confirm(`确认删除供应商“${provider.name}”吗？\n\n如果存在已结束的会话历史，也会一起删除；运行中的会话必须先停止。`)) return;
    try {
      await api(`/api/providers/${id}?force=true`, { method: 'DELETE' });
      if (providerEditId === id) resetProviderEditor();
      toast('供应商及其非活动会话历史已删除');
      await Promise.all([loadProviders(), loadSessions(), loadDashboard(), loadLogs()]);
    } catch (error) {
      toast(error.message, true);
    }
  };

  window.editSession = id => {
    const session = state.sessions.find(row => row.id === id);
    if (!session) return toast('未找到会话', true);
    sessionEditId = id;
    const form = sessionForm();
    form.querySelector('[name="name"]').value = sessionName(session);
    form.querySelector('[name="provider_config_id"]').value = session.provider_config_id;
    form.querySelector('[name="bridge_id"]').value = session.bridge_id || '';
    form.querySelector('[name="overrides"]').value = JSON.stringify(sessionOverrides(session), null, 2);
    const active = ACTIVE_SESSION_STATUSES.has(session.status);
    form.querySelector('[name="provider_config_id"]').disabled = active;
    form.querySelector('[name="bridge_id"]').disabled = active;
    form.querySelector('[name="overrides"]').disabled = active;
    document.getElementById('session-form-title').textContent = `修改会话：${sessionName(session)}`;
    document.getElementById('session-save-button').textContent = active ? '保存名称' : '保存修改';
    document.getElementById('session-edit-cancel').hidden = false;
    if (active) toast('运行中的会话只能修改名称；停止后可修改供应商、Bridge 和覆盖参数。');
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  window.restartSession = async id => {
    try {
      await api(`/api/sessions/${id}/restart`, { method: 'POST' });
      toast('会话已重新启用');
      await Promise.all([loadSessions(), loadDashboard(), loadLogs()]);
    } catch (error) {
      toast(error.message, true);
    }
  };

  window.deleteSession = async id => {
    const session = state.sessions.find(row => row.id === id);
    if (!session || !confirm(`确认删除会话“${sessionName(session)}”吗？此操作只删除会话记录，不删除供应商。`)) return;
    try {
      await api(`/api/sessions/${id}`, { method: 'DELETE' });
      if (sessionEditId === id) resetSessionEditor();
      toast('会话已删除');
      await Promise.all([loadSessions(), loadDashboard(), loadLogs()]);
    } catch (error) {
      toast(error.message, true);
    }
  };

  window.editDirectorCommand = id => {
    const command = directorState.commands.find(row => row.id === id);
    if (!command) return toast('未找到导演命令', true);
    directorEditId = id;
    const form = document.getElementById('director-command-form');
    form.querySelector('[name="extension_id"]').value = command.extension_id;
    form.querySelector('[name="command_type"]').value = command.command_type;
    form.querySelector('[name="content"]').value = commandRawContent(command);
    form.querySelector('[name="wrap_as_director"]').checked =
      command.payload?.wrap_as_director ?? String(command.payload?.text || '').startsWith('【导演指令】');
    form.querySelector('[name="auto_send"]').checked = command.payload?.auto_send !== false;
    form.querySelector('[name="force"]').checked = Boolean(command.payload?.force);
    form.querySelector('[name="priority"]').value = command.priority;
    document.getElementById('director-editor-title').textContent = '编辑导演命令（保存后重新发送）';
    form.querySelector('button[type="submit"]').textContent = '保存并重新发送';
    document.getElementById('director-edit-cancel').hidden = false;
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  window.deleteDirectorCommand = async id => {
    if (!confirm('确认删除这条导演命令吗？')) return;
    try {
      await api(`/api/director/commands/${id}`, { method: 'DELETE' });
      if (directorEditId === id) resetDirectorEditor();
      toast('导演命令已删除');
      await loadDirector();
    } catch (error) {
      toast(error.message, true);
    }
  };

  function installProviderEditor() {
    const form = providerForm();
    const title = form.closest('.panel')?.querySelector('h2');
    if (title) title.id = 'provider-form-title';
    const submit = form.querySelector('button[type="submit"]');
    submit.id = 'provider-save-button';
    const cancel = document.createElement('button');
    cancel.id = 'provider-edit-cancel';
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = '取消修改';
    cancel.hidden = true;
    cancel.addEventListener('click', resetProviderEditor);
    submit.after(cancel);

    form.addEventListener('submit', event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      (async () => {
        const credentialsText = form.querySelector('[name="credentials"]').value.trim();
        const body = {
          name: form.querySelector('[name="name"]').value.trim(),
          api_base_url: form.querySelector('[name="api_base_url"]').value.trim() || null,
          settings: parseJson(form.querySelector('[name="settings"]').value || '', {}),
        };
        if (!providerEditId) {
          body.provider_type = form.querySelector('[name="provider_type"]').value;
          body.credentials = parseJson(credentialsText, {});
        } else if (credentialsText) {
          body.credentials = parseJson(credentialsText, {});
        }
        await api(providerEditId ? `/api/providers/${providerEditId}` : '/api/providers', {
          method: providerEditId ? 'PATCH' : 'POST',
          body: JSON.stringify(body),
        });
        toast(providerEditId ? '供应商修改已保存' : '供应商已保存');
        resetProviderEditor();
        await Promise.all([loadProviders(), loadDashboard(), loadLogs()]);
      })().catch(error => toast(error.message, true));
    }, true);
  }

  function installSessionEditor() {
    const form = sessionForm();
    const title = form.closest('.panel')?.querySelector('h2');
    if (title) title.id = 'session-form-title';

    const nameLabel = document.createElement('label');
    nameLabel.innerHTML = '会话名称<input name="name" maxlength="120" required>';
    form.prepend(nameLabel);

    const submit = form.querySelector('button[type="submit"]');
    submit.id = 'session-save-button';
    const cancel = document.createElement('button');
    cancel.id = 'session-edit-cancel';
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = '取消修改';
    cancel.hidden = true;
    cancel.addEventListener('click', resetSessionEditor);
    submit.after(cancel);

    form.addEventListener('submit', event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      (async () => {
        const name = form.querySelector('[name="name"]').value.trim();
        if (!name) throw new Error('会话名称不能为空');
        const active = sessionEditId
          ? ACTIVE_SESSION_STATUSES.has(state.sessions.find(row => row.id === sessionEditId)?.status)
          : false;
        if (sessionEditId) {
          const body = { name };
          if (!active) {
            body.provider_config_id = form.querySelector('[name="provider_config_id"]').value;
            body.bridge_id = form.querySelector('[name="bridge_id"]').value || null;
            body.overrides = parseJson(form.querySelector('[name="overrides"]').value || '', {});
          }
          await api(`/api/sessions/${sessionEditId}`, {
            method: 'PATCH',
            body: JSON.stringify(body),
          });
          toast('会话修改已保存');
        } else {
          const overrides = parseJson(form.querySelector('[name="overrides"]').value || '', {});
          overrides[SESSION_NAME_KEY] = name;
          await api('/api/sessions', {
            method: 'POST',
            body: JSON.stringify({
              provider_config_id: form.querySelector('[name="provider_config_id"]').value,
              bridge_id: form.querySelector('[name="bridge_id"]').value || null,
              overrides,
            }),
          });
          toast('会话启动请求已完成');
        }
        resetSessionEditor();
        await Promise.all([loadSessions(), loadDashboard(), loadLogs()]);
      })().catch(error => toast(error.message, true));
    }, true);

    resetSessionEditor();
  }

  function installDirectorEditor() {
    const form = document.getElementById('director-command-form');
    const title = form.closest('.panel')?.querySelector('h2');
    if (title) title.id = 'director-editor-title';
    const actions = form.querySelector('.actions');
    const cancel = document.createElement('button');
    cancel.id = 'director-edit-cancel';
    cancel.type = 'button';
    cancel.className = 'secondary';
    cancel.textContent = '取消编辑';
    cancel.hidden = true;
    cancel.addEventListener('click', resetDirectorEditor);
    actions.appendChild(cancel);
  }

  function mappingSnapshot() {
    const pre = document.getElementById('vtube-live-json');
    if (!pre) return null;
    try {
      return JSON.parse(pre.textContent || '{}');
    } catch (_) {
      return null;
    }
  }

  function mappingPanel() {
    let panel = document.getElementById('vtube-semantic-mapping-editor');
    if (panel) return panel;
    const result = document.getElementById('vtube-action-result');
    if (!result) return null;
    panel = document.createElement('div');
    panel.id = 'vtube-semantic-mapping-editor';
    panel.className = 'avatar-mapping-editor';
    panel.innerHTML = `
      <h4>动作热键映射</h4>
      <p class="hint">必须选择当前模型真实存在的热键。建议直接保存 Hotkey ID，修改后会同时写入供应商并立即应用到当前会话，无需重启。</p>
      <div id="vtube-mapping-fields" class="avatar-mapping-fields"></div>
      <div class="actions">
        <button id="vtube-mapping-auto" type="button" class="secondary">按当前 3 个动作自动填充</button>
        <button id="vtube-mapping-clear" type="button" class="secondary">清空映射</button>
        <button id="vtube-mapping-save" type="button">保存并立即应用</button>
      </div>
      <div id="vtube-mapping-status" class="diagnosis warn">尚未保存动作映射。</div>
    `;
    result.after(panel);
    panel.addEventListener('change', event => {
      if (event.target.matches('[data-vtube-map]')) mappingDirty = true;
    });
    panel.querySelector('#vtube-mapping-auto').addEventListener('click', () => {
      const data = mappingSnapshot();
      const hotkeys = data?.runtime?.hotkeys || [];
      const assign = {
        wave: hotkeys[0]?.hotkeyID || '',
        happy: hotkeys[1]?.hotkeyID || '',
        thinking: hotkeys[2]?.hotkeyID || '',
      };
      Object.entries(assign).forEach(([key, value]) => {
        const select = panel.querySelector(`[data-vtube-map="${key}"]`);
        if (select) select.value = value;
      });
      mappingDirty = true;
      panel.querySelector('#vtube-mapping-status').textContent =
        '已按顺序填入：动作1=挥手、动作2=开心、动作3=思考。请逐个测试，确认语义后再保存。';
    });
    panel.querySelector('#vtube-mapping-clear').addEventListener('click', () => {
      panel.querySelectorAll('[data-vtube-map]').forEach(select => { select.value = ''; });
      mappingDirty = true;
    });
    panel.querySelector('#vtube-mapping-save').addEventListener('click', () => {
      saveVTubeMapping().catch(error => toast(error.message, true));
    });
    return panel;
  }

  function renderMappingEditor() {
    const data = mappingSnapshot();
    const runtime = data?.runtime;
    if (!runtime || !runtime.session_id) return;
    const panel = mappingPanel();
    if (!panel) return;
    const hotkeys = Array.isArray(runtime.hotkeys) ? runtime.hotkeys : [];
    const current = runtime.config?.hotkeys || {};
    const container = panel.querySelector('#vtube-mapping-fields');

    const preserved = {};
    if (mappingDirty) {
      container.querySelectorAll('[data-vtube-map]').forEach(select => {
        preserved[select.dataset.vtubeMap] = select.value;
      });
    }

    container.innerHTML = Object.entries(ACTION_LABELS).map(([key, label]) => {
      const configured = preserved[key] ?? current[key] ?? '';
      const match = hotkeys.find(item =>
        item.hotkeyID === configured
        || String(item.name || '').toLowerCase() === String(configured).toLowerCase()
      );
      const selectedValue = match?.hotkeyID || configured;
      const invalid = configured && !match;
      const options = [
        '<option value="">不映射</option>',
        ...hotkeys.map(item => `<option value="${escapeHtml(item.hotkeyID || '')}" ${item.hotkeyID === selectedValue ? 'selected' : ''}>${escapeHtml(item.name || item.hotkeyID)} · ${escapeHtml(item.type || '')}</option>`),
      ];
      if (invalid) options.push(`<option value="${escapeHtml(configured)}" selected>无效映射：${escapeHtml(configured)}</option>`);
      return `<label>${label}<select data-vtube-map="${key}">${options.join('')}</select></label>`;
    }).join('');

    const invalidNames = Object.values(current).filter(value => value && !hotkeys.some(item =>
      item.hotkeyID === value || String(item.name || '').toLowerCase() === String(value).toLowerCase()
    ));
    const status = panel.querySelector('#vtube-mapping-status');
    if (invalidNames.length) {
      status.textContent = `发现不存在于当前模型的热键：${invalidNames.join('、')}。请选择右侧实际热键后保存。`;
      status.className = 'diagnosis bad';
    } else if (!mappingDirty) {
      status.textContent = '当前映射均可在模型热键列表中解析。';
      status.className = 'diagnosis good';
    }
  }

  async function saveVTubeMapping() {
    const data = mappingSnapshot();
    const runtime = data?.runtime;
    const provider = data?.provider;
    const session = data?.session;
    if (!runtime?.session_id || !provider?.id || !session?.bridge_id) {
      throw new Error('当前 VTube Studio 会话信息不完整');
    }
    const panel = mappingPanel();
    const hotkeys = {};
    panel.querySelectorAll('[data-vtube-map]').forEach(select => {
      hotkeys[select.dataset.vtubeMap] = select.value || '';
    });
    const settings = {
      ...(provider.settings || {}),
      hotkeys,
    };
    await api(`/api/providers/${provider.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ settings }),
    });
    await sendBridgeCommand(
      session.bridge_id,
      'provider.vtube_studio.configure',
      {
        session_id: runtime.session_id,
        hotkeys,
        action_cooldown_ms: runtime.config?.action_cooldown_ms ?? 1200,
      },
      15,
    );
    mappingDirty = false;
    panel.querySelector('#vtube-mapping-status').textContent = '动作映射已保存到供应商，并立即应用到当前会话。';
    panel.querySelector('#vtube-mapping-status').className = 'diagnosis good';
    await loadProviders();
    document.getElementById('avatar-debug-refresh')?.click();
    toast('VTube Studio 动作映射已保存并立即生效');
  }

  function installMappingObserver() {
    const target = document.getElementById('vtube-live-json');
    if (!target) {
      setTimeout(installMappingObserver, 250);
      return;
    }
    new MutationObserver(renderMappingEditor).observe(target, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    renderMappingEditor();
  }

  function addStyles() {
    if (document.getElementById('aliver-management-v2-style')) return;
    const style = document.createElement('style');
    style.id = 'aliver-management-v2-style';
    style.textContent = `
      .avatar-mapping-editor { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border, #263241); }
      .avatar-mapping-editor h4 { margin: 0 0 6px; }
      .avatar-mapping-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 12px 0; }
      .avatar-mapping-fields label { margin: 0; }
      .avatar-mapping-fields select { width: 100%; }
    `;
    document.head.appendChild(style);
  }

  function start() {
    if (
      !providerForm()
      || !sessionForm()
      || !document.getElementById('director-command-form')
      || typeof loadDirector !== 'function'
      || typeof directorState === 'undefined'
    ) {
      setTimeout(start, 100);
      return;
    }
    if (document.getElementById('provider-edit-cancel')) return;

    addStyles();
    loadProviders = managedLoadProviders;
    loadSessions = managedLoadSessions;
    renderDirectorCommands = managedRenderDirectorCommands;
    sendDirectorCommand = managedSendDirectorCommand;

    installProviderEditor();
    installSessionEditor();
    installDirectorEditor();
    installMappingObserver();

    Promise.all([loadProviders(), loadSessions(), loadDirector()]).catch(error => toast(error.message, true));
  }

  start();
})();
