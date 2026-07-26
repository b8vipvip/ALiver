const state = {
  providers: [],
  bridges: [],
  sessions: [],
  audioDevices: [],
  audioOutputs: [],
  audioStatus: null,
  audioPollInFlight: false,
};

function headers(json = false) {
  const h = {};
  const token = localStorage.getItem('aliverAdminToken');
  if (token) h['X-ALiver-Token'] = token;
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

async function api(path, options = {}) {
  options.headers = { ...headers(Boolean(options.body)), ...(options.headers || {}) };
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.style.borderColor = isError ? 'var(--bad)' : 'var(--good)';
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 4500);
}

function parseJson(text, fallback = {}) {
  if (!text.trim()) return fallback;
  return JSON.parse(text);
}

function statusBadge(value) {
  const good = ['active', 'running', 'ready', 'online', 'ended'];
  const warn = ['starting', 'awaiting_manual', 'offline', 'ended_local_only'];
  const cls = good.includes(value) ? 'good' : warn.includes(value) ? 'warn' : 'bad';
  return `<span class="badge ${cls}">${value}</span>`;
}

async function loadHealth() {
  try {
    const value = await api('/api/health');
    const el = document.getElementById('health');
    el.textContent = `${value.status} · ${value.version}`;
    el.className = 'badge good';
  } catch (error) {
    const el = document.getElementById('health');
    el.textContent = '服务异常';
    el.className = 'badge bad';
  }
}

async function loadDashboard() {
  const value = await api('/api/dashboard');
  document.getElementById('metric-providers').textContent = value.providers;
  document.getElementById('metric-sessions').textContent = value.active_sessions;
  document.getElementById('metric-bridges').textContent = value.online_bridges;
  document.getElementById('metric-errors').textContent = value.errors;
}

async function loadProviders() {
  state.providers = await api('/api/providers');
  const list = document.getElementById('provider-list');
  list.innerHTML = state.providers.map(p => `
    <div class="item">
      <div class="item-head">
        <div><h3>${escapeHtml(p.name)}</h3><div class="meta">${p.provider_type} · ${p.execution_mode} · ${p.id}</div></div>
        ${statusBadge(p.enabled ? 'active' : 'disabled')}
      </div>
      <div class="meta">密钥字段：${p.credential_keys.join(', ') || '无'}</div>
      <pre>${escapeHtml(JSON.stringify(p.settings, null, 2))}</pre>
      <div class="actions">
        <button onclick="testProvider('${p.id}')">测试连接</button>
        <button class="secondary" onclick="toggleProvider('${p.id}', ${!p.enabled})">${p.enabled ? '禁用' : '启用'}</button>
      </div>
    </div>`).join('') || '<p class="hint">暂无供应商。</p>';
  const select = document.getElementById('session-provider');
  select.innerHTML = state.providers
    .filter(p => p.enabled)
    .map(p => `<option value="${p.id}">${escapeHtml(p.name)}（${p.provider_type}）</option>`)
    .join('');
}

function syncAudioBridgeOptions() {
  const select = document.getElementById('audio-bridge');
  if (!select) return;
  const previous = select.value;
  const online = state.bridges.filter(bridge => bridge.connected);
  select.innerHTML = '<option value="">请选择在线 Bridge</option>' + online
    .map(bridge => `<option value="${bridge.id}">${escapeHtml(bridge.name)} · ${escapeHtml(bridge.machine_name)}</option>`)
    .join('');
  if (online.some(bridge => bridge.id === previous)) {
    select.value = previous;
  } else if (online.length === 1) {
    select.value = online[0].id;
  }
}

async function loadBridges() {
  state.bridges = await api('/api/bridges');
  document.getElementById('bridge-list').innerHTML = state.bridges.map(b => `
    <div class="item">
      <div class="item-head">
        <div><h3>${escapeHtml(b.name)}</h3><div class="meta">${escapeHtml(b.machine_name)} · ${escapeHtml(b.version)} · ${b.id}</div></div>
        ${statusBadge(b.connected ? 'online' : 'offline')}
      </div>
      <div class="meta">能力：${b.capabilities.join(', ') || '未上报'} · 最后心跳：${formatTime(b.last_seen_at)}</div>
      <div class="actions">
        <button ${b.connected ? '' : 'disabled'} onclick="bridgeCommand('${b.id}', 'ping')">Ping</button>
        <button class="secondary" ${b.connected ? '' : 'disabled'} onclick="bridgeCommand('${b.id}', 'system.info')">系统信息</button>
        <button class="secondary" ${b.connected ? '' : 'disabled'} onclick="bridgeCommand('${b.id}', 'process.list')">进程列表</button>
      </div>
    </div>`).join('') || '<p class="hint">暂无 Bridge。运行 bridge/agent.py 后会自动注册。</p>';
  const select = document.getElementById('session-bridge');
  select.innerHTML = '<option value="">不使用</option>' + state.bridges
    .filter(b => b.connected)
    .map(b => `<option value="${b.id}">${escapeHtml(b.name)}</option>`)
    .join('');
  syncAudioBridgeOptions();
}

async function loadSessions() {
  state.sessions = await api('/api/sessions');
  document.getElementById('session-list').innerHTML = state.sessions.map(s => `
    <div class="item">
      <div class="item-head">
        <div><h3>${escapeHtml(s.provider_name || s.provider_config_id)}</h3><div class="meta">${s.provider_type || ''} · ${s.id}</div></div>
        ${statusBadge(s.status)}
      </div>
      <div class="meta">外部会话：${escapeHtml(s.external_session_id || '无')} · Bridge：${escapeHtml(s.bridge_id || '无')}</div>
      ${s.error_message ? `<pre>${escapeHtml(s.error_message)}</pre>` : ''}
      <div class="actions">
        <button class="danger" ${['ended', 'ended_local_only'].includes(s.status) ? 'disabled' : ''} onclick="stopSession('${s.id}')">停止</button>
      </div>
    </div>`).join('') || '<p class="hint">暂无会话。</p>';
}

async function loadLogs() {
  const [logs, summary] = await Promise.all([
    api('/api/logs?limit=150'),
    api('/api/logs/summary'),
  ]);
  document.getElementById('log-summary').textContent =
    `级别：${JSON.stringify(summary.levels)} · 延迟：${JSON.stringify(summary.latency_ms)}`;
  document.getElementById('log-list').innerHTML = logs.map(row => `
    <div class="log-row">
      <span>${formatTime(row.created_at)}</span>
      <span class="badge ${row.level === 'ERROR' ? 'bad' : 'good'}">${row.level}</span>
      <span>${escapeHtml(row.category)}</span>
      <span>${escapeHtml(row.message)}</span>
      <span>${row.latency_ms == null ? '' : row.latency_ms + ' ms'}</span>
    </div>`).join('');
}

async function refreshAll() {
  try {
    await Promise.all([
      loadHealth(),
      loadDashboard(),
      loadProviders(),
      loadBridges(),
      loadSessions(),
      loadLogs(),
    ]);
  } catch (error) {
    toast(error.message, true);
  }
}

async function sendBridgeCommand(id, commandType, payload = {}, timeoutSeconds = 30) {
  const value = await api(`/api/bridges/${id}/commands`, {
    method: 'POST',
    body: JSON.stringify({
      command_type: commandType,
      payload,
      timeout_seconds: timeoutSeconds,
    }),
  });
  if (value && value.type === 'result') {
    if (!value.ok) throw new Error(value.error || `${commandType} failed`);
    return value.data || {};
  }
  return value && value.data !== undefined ? value.data : value;
}

function selectedAudioBridge() {
  const id = document.getElementById('audio-bridge').value;
  if (!id) throw new Error('请先选择一个在线 Bridge');
  return id;
}

function deviceLabel(device) {
  const type = device.is_loopback ? '回放' : device.kind === 'input' ? '输入' : '输出';
  const defaults = [
    device.is_default_loopback ? '默认回放' : '',
    device.is_default_input ? '默认输入' : '',
    device.is_default_output ? '默认输出' : '',
  ].filter(Boolean).join('/');
  const suffix = defaults ? ` · ${defaults}` : '';
  return `[${type}] ${device.name} · ${device.default_sample_rate}Hz · ${device.input_channels || device.output_channels}ch${suffix}`;
}

function renderDeviceRows(targetId, devices) {
  const target = document.getElementById(targetId);
  target.innerHTML = devices.map(device => `
    <div class="device-row ${device.is_default_loopback || device.is_default_output ? 'selected' : ''}">
      <strong>#${device.index} ${escapeHtml(device.name)}</strong>
      <span>${escapeHtml(deviceLabel(device))}</span>
    </div>
  `).join('') || '<p class="hint">没有发现对应设备。</p>';
}

function renderAudioDevices(data) {
  state.audioDevices = data.capture_devices || [];
  state.audioOutputs = data.output_devices || [];
  const select = document.getElementById('audio-device');
  const previous = select.value;
  select.innerHTML = state.audioDevices.map(device => `
    <option value="${device.index}">${escapeHtml(deviceLabel(device))}</option>
  `).join('') || '<option value="">没有可捕获设备</option>';

  if (state.audioDevices.some(device => String(device.index) === previous)) {
    select.value = previous;
  } else {
    const preferred = state.audioDevices.find(device => device.is_default_loopback)
      || state.audioDevices.find(device => device.is_loopback)
      || state.audioDevices[0];
    if (preferred) select.value = String(preferred.index);
  }
  renderDeviceRows('audio-device-list', state.audioDevices);
  renderDeviceRows('audio-output-list', state.audioOutputs);
}

function renderAudioStatus(value) {
  state.audioStatus = value;
  const active = Boolean(value && value.active);
  const badge = document.getElementById('audio-state-badge');
  badge.textContent = active ? '捕获中' : value && value.error ? '错误' : '已停止';
  badge.className = `badge ${active ? 'good' : value && value.error ? 'bad' : 'warn'}`;

  const dbfs = Number(value && value.dbfs != null ? value.dbfs : -96);
  const percent = Math.max(0, Math.min(100, ((dbfs + 60) / 60) * 100));
  document.getElementById('audio-meter-fill').style.width = `${percent}%`;
  document.getElementById('audio-dbfs').textContent = `${dbfs.toFixed(1)} dBFS`;
  const elapsed = Number(value && value.elapsed_seconds != null ? value.elapsed_seconds : 0);
  document.getElementById('audio-elapsed').textContent = `${elapsed.toFixed(1)} 秒`;
  document.getElementById('audio-status-json').textContent = JSON.stringify(value || {}, null, 2);
}

async function scanAudioDevices() {
  const bridgeId = selectedAudioBridge();
  const data = await sendBridgeCommand(bridgeId, 'audio.devices', {}, 30);
  renderAudioDevices(data);
  toast(`已扫描到 ${state.audioDevices.length} 个可捕获设备`);
}

async function readAudioStatus(showToast = false) {
  const bridgeId = selectedAudioBridge();
  const data = await sendBridgeCommand(bridgeId, 'audio.capture.status', {}, 10);
  renderAudioStatus(data);
  if (showToast) toast('音频状态已更新');
}

async function startAudioCapture() {
  const bridgeId = selectedAudioBridge();
  const deviceIndex = document.getElementById('audio-device').value;
  if (!deviceIndex) throw new Error('请先扫描并选择捕获设备');
  const wavSeconds = Number(document.getElementById('audio-wav-seconds').value || 10);
  const saveWav = document.getElementById('audio-save-wav').checked;
  const data = await sendBridgeCommand(
    bridgeId,
    'audio.capture.start',
    {
      device_index: Number(deviceIndex),
      chunk_size: 1024,
      save_wav: saveWav,
      wav_seconds: wavSeconds,
    },
    15,
  );
  renderAudioStatus(data);
  toast('音频捕获已启动，请让 ChatGPT Live 说一句话');
}

async function stopAudioCapture() {
  const bridgeId = selectedAudioBridge();
  const data = await sendBridgeCommand(bridgeId, 'audio.capture.stop', {}, 15);
  renderAudioStatus(data);
  toast(data.wav_path ? `捕获已停止，测试文件：${data.wav_path}` : '音频捕获已停止');
}

async function pollAudioStatus() {
  const panel = document.getElementById('tab-audio');
  const bridgeId = document.getElementById('audio-bridge').value;
  if (!panel.classList.contains('active') || !bridgeId || state.audioPollInFlight) return;
  state.audioPollInFlight = true;
  try {
    const data = await sendBridgeCommand(bridgeId, 'audio.capture.status', {}, 8);
    renderAudioStatus(data);
  } catch (_) {
    // Bridge refresh handles connectivity; do not show one toast per second.
  } finally {
    state.audioPollInFlight = false;
  }
}

window.testProvider = async id => {
  try {
    const value = await api(`/api/providers/${id}/test`, { method: 'POST' });
    toast(JSON.stringify(value));
    await loadLogs();
  } catch (error) {
    toast(error.message, true);
  }
};

window.toggleProvider = async (id, enabled) => {
  try {
    await api(`/api/providers/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
    await loadProviders();
  } catch (error) {
    toast(error.message, true);
  }
};

window.stopSession = async id => {
  try {
    await api(`/api/sessions/${id}/stop`, { method: 'POST' });
    await Promise.all([loadSessions(), loadDashboard(), loadLogs()]);
  } catch (error) {
    toast(error.message, true);
  }
};

window.bridgeCommand = async (id, commandType) => {
  try {
    const value = await sendBridgeCommand(id, commandType);
    toast(JSON.stringify(value));
  } catch (error) {
    toast(error.message, true);
  }
};

function escapeHtml(value) {
  return String(value).replace(
    /[&<>'"]/g,
    ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[ch],
  );
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '无';
}

document.querySelectorAll('.tabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  button.classList.add('active');
  document.getElementById(`tab-${button.dataset.tab}`).classList.add('active');
  if (button.dataset.tab === 'audio') {
    pollAudioStatus();
  }
}));

document.querySelectorAll('[data-refresh]').forEach(button => button.addEventListener('click', () => {
  const name = button.dataset.refresh;
  ({ providers: loadProviders, sessions: loadSessions, bridges: loadBridges, logs: loadLogs })[name]()
    .catch(error => toast(error.message, true));
}));

document.getElementById('provider-form').addEventListener('submit', async event => {
  event.preventDefault();
  const f = new FormData(event.target);
  try {
    await api('/api/providers', {
      method: 'POST',
      body: JSON.stringify({
        name: f.get('name'),
        provider_type: f.get('provider_type'),
        api_base_url: f.get('api_base_url') || null,
        credentials: parseJson(f.get('credentials') || '', {}),
        settings: parseJson(f.get('settings') || '', {}),
      }),
    });
    event.target.reset();
    toast('供应商已保存');
    await Promise.all([loadProviders(), loadDashboard(), loadLogs()]);
  } catch (error) {
    toast(error.message, true);
  }
});

document.getElementById('session-form').addEventListener('submit', async event => {
  event.preventDefault();
  const f = new FormData(event.target);
  try {
    await api('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        provider_config_id: f.get('provider_config_id'),
        bridge_id: f.get('bridge_id') || null,
        overrides: parseJson(f.get('overrides') || '', {}),
      }),
    });
    toast('会话启动请求已完成');
    await Promise.all([loadSessions(), loadDashboard(), loadLogs()]);
  } catch (error) {
    toast(error.message, true);
  }
});

document.getElementById('audio-scan').addEventListener('click', () => {
  scanAudioDevices().catch(error => toast(error.message, true));
});
document.getElementById('audio-status').addEventListener('click', () => {
  readAudioStatus(true).catch(error => toast(error.message, true));
});
document.getElementById('audio-start').addEventListener('click', () => {
  startAudioCapture().catch(error => toast(error.message, true));
});
document.getElementById('audio-stop').addEventListener('click', () => {
  stopAudioCapture().catch(error => toast(error.message, true));
});
document.getElementById('audio-bridge').addEventListener('change', () => {
  state.audioDevices = [];
  state.audioOutputs = [];
  document.getElementById('audio-device').innerHTML =
    '<option value="">请扫描当前 Bridge 的设备</option>';
  renderAudioStatus({});
});

document.getElementById('admin-token').value =
  localStorage.getItem('aliverAdminToken') || '';
document.getElementById('save-token').addEventListener('click', () => {
  localStorage.setItem(
    'aliverAdminToken',
    document.getElementById('admin-token').value.trim(),
  );
  toast('令牌已保存到当前浏览器');
  refreshAll();
});

refreshAll();
setInterval(
  () => Promise.all([loadDashboard(), loadBridges()]).catch(() => {}),
  8000,
);
setInterval(pollAudioStatus, 1000);
