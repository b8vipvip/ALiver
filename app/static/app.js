const state = {
  providers: [],
  bridges: [],
  sessions: [],
  audioScan: null,
  routeStatus: null,
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
  setTimeout(() => el.classList.remove('show'), 5000);
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

function escapeHtml(value) {
  return String(value).replace(
    /[&<>'"]/g,
    ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[ch],
  );
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '无';
}

async function loadHealth() {
  try {
    const value = await api('/api/health');
    const el = document.getElementById('health');
    el.textContent = `${value.status} · ${value.version}`;
    el.className = 'badge good';
  } catch (_) {
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
  document.getElementById('session-provider').innerHTML = state.providers
    .filter(p => p.enabled)
    .map(p => `<option value="${p.id}">${escapeHtml(p.name)}（${p.provider_type}）</option>`)
    .join('');
}

function syncAudioBridgeOptions() {
  const select = document.getElementById('audio-bridge');
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
  document.getElementById('session-bridge').innerHTML = '<option value="">不使用</option>' + state.bridges
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
      loadHealth(), loadDashboard(), loadProviders(), loadBridges(), loadSessions(), loadLogs(),
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

function routeDeviceLabel(device) {
  const virtual = device.is_virtual ? ` · ${device.virtual_family}` : '';
  return `${device.name} · ${device.default_sample_rate}Hz · ${device.input_channels || device.output_channels}ch${virtual}`;
}

function findPair(family) {
  return (state.audioScan?.virtual_pairs || []).find(pair => pair.family === family) || null;
}

function optionHtml(devices, selectedKey, role) {
  const rows = devices.filter(device => device.is_virtual);
  return '<option value="">请选择虚拟设备</option>' + rows.map(device => {
    const selected = device.key === selectedKey ? ' selected' : '';
    return `<option value="${device.key}"${selected}>${escapeHtml(`[${role}] ${routeDeviceLabel(device)}`)}</option>`;
  }).join('');
}

function renderRouteWarnings(warnings = []) {
  document.getElementById('route-warning-list').innerHTML = warnings.length
    ? warnings.map(message => `<div class="diagnosis warn">${escapeHtml(message)}</div>`).join('')
    : '<div class="diagnosis good">未发现路由风险。</div>';
}

function updateRouteHints() {
  const outKey = document.getElementById('gpt-out-device').value;
  const inKey = document.getElementById('gpt-in-device').value;
  const outDevice = (state.audioScan?.loopback_devices || []).find(row => row.key === outKey);
  const inDevice = (state.audioScan?.output_devices || []).find(row => row.key === inKey);
  const outPair = outDevice ? findPair(outDevice.virtual_family) : null;
  const inPair = inDevice ? findPair(inDevice.virtual_family) : null;
  document.getElementById('gpt-out-playback-hint').textContent =
    outPair?.playback?.name || '未找到对应虚拟扬声器';
  document.getElementById('gpt-in-microphone-hint').textContent =
    inPair?.microphone?.name || '未找到对应虚拟麦克风';

  const warnings = [];
  if (outDevice && inDevice && outDevice.virtual_family === inDevice.virtual_family) {
    warnings.push('GPT_IN 和 GPT_OUT 选择了同一虚拟声卡，会产生回灌。');
  }
  if (inDevice && !inPair?.microphone) warnings.push('GPT_IN 所选设备没有匹配的虚拟麦克风端点。');
  renderRouteWarnings(warnings);
}

function renderVirtualPairs(pairs = []) {
  document.getElementById('virtual-pair-list').innerHTML = pairs.map(pair => `
    <div class="pair-card ${pair.complete ? 'complete' : ''}">
      <div class="item-head"><h3>${escapeHtml(pair.family)}</h3>${statusBadge(pair.complete ? 'ready' : 'incomplete')}</div>
      <div><strong>播放：</strong>${escapeHtml(pair.playback?.name || '缺失')}</div>
      <div><strong>回放：</strong>${escapeHtml(pair.loopback?.name || '缺失')}</div>
      <div><strong>麦克风：</strong>${escapeHtml(pair.microphone?.name || '缺失')}</div>
    </div>`).join('') || '<p class="hint">未检测到 VB-CABLE、VoiceMeeter 等虚拟声卡。</p>';
}

function renderRouteStatus(routeStatus) {
  state.routeStatus = routeStatus;
  const ready = Boolean(routeStatus?.ready);
  const isolated = Boolean(routeStatus?.isolated);
  const routeBadge = document.getElementById('route-ready-badge');
  routeBadge.textContent = ready ? '双通道就绪' : isolated ? '部分就绪' : '未就绪';
  routeBadge.className = `badge ${ready ? 'good' : 'warn'}`;

  const outBadge = document.getElementById('gpt-out-badge');
  outBadge.textContent = routeStatus?.gpt_out?.ready ? '已配置' : '未配置';
  outBadge.className = `badge ${routeStatus?.gpt_out?.ready ? 'good' : 'warn'}`;
  const inBadge = document.getElementById('gpt-in-badge');
  inBadge.textContent = routeStatus?.gpt_in?.ready ? '已配置' : '未配置';
  inBadge.className = `badge ${routeStatus?.gpt_in?.ready ? 'good' : 'warn'}`;

  const out = routeStatus?.gpt_out?.capture;
  const input = routeStatus?.gpt_in?.playback;
  if (out) document.getElementById('gpt-out-device').value = out.key;
  if (input) document.getElementById('gpt-in-device').value = input.key;
  document.getElementById('gpt-out-playback-hint').textContent =
    routeStatus?.gpt_out?.playback?.name || '尚未匹配';
  document.getElementById('gpt-in-microphone-hint').textContent =
    routeStatus?.gpt_in?.microphone?.name || '尚未匹配';
  renderRouteWarnings(routeStatus?.warnings || []);
}

function renderAudioScan(data) {
  state.audioScan = data;
  const configured = data.routes?.configured || {};
  document.getElementById('gpt-out-device').innerHTML = optionHtml(
    data.loopback_devices || [], configured.gpt_out?.capture_device_key, 'GPT_OUT 回放',
  );
  document.getElementById('gpt-in-device').innerHTML = optionHtml(
    data.output_devices || [], configured.gpt_in?.playback_device_key, 'GPT_IN 写入',
  );
  renderVirtualPairs(data.virtual_pairs || []);
  renderRouteStatus(data.routes || {});
  updateRouteHints();
}

async function scanRoutes() {
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.routes.scan', {}, 30);
  renderAudioScan(data);
  const virtualCount = (data.virtual_pairs || []).length;
  toast(`扫描完成：发现 ${virtualCount} 组虚拟声卡`);
}

async function autoConfigureRoutes() {
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.routes.auto', {}, 30);
  await scanRoutes();
  renderRouteStatus(data);
  toast('已自动选择两条相互隔离的虚拟声卡并保存');
}

async function saveRoutes() {
  const outKey = document.getElementById('gpt-out-device').value;
  const inKey = document.getElementById('gpt-in-device').value;
  if (!outKey || !inKey) throw new Error('请分别选择 GPT_OUT 和 GPT_IN 虚拟设备');
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.routes.save', {
    gpt_out_capture_key: outKey,
    gpt_in_playback_key: inKey,
  }, 30);
  renderRouteStatus(data);
  toast('双虚拟声卡路由已保存');
}

async function refreshRoutes() {
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.routes.get', {}, 30);
  renderRouteStatus(data);
  toast('已读取 Bridge 保存的音频路由');
}

function renderGptOutStatus(value) {
  const active = Boolean(value?.active);
  const dbfs = Number(value?.dbfs ?? -96);
  const maxDbfs = Number(value?.max_dbfs ?? -96);
  const percent = Math.max(0, Math.min(100, ((dbfs + 60) / 60) * 100));
  document.getElementById('gpt-out-meter-fill').style.width = `${percent}%`;
  document.getElementById('gpt-out-dbfs').textContent = `${dbfs.toFixed(1)} dBFS`;
  document.getElementById('gpt-out-max-dbfs').textContent = `${maxDbfs.toFixed(1)} dBFS`;
  document.getElementById('gpt-out-elapsed').textContent = `${Number(value?.elapsed_seconds || 0).toFixed(1)} 秒`;
  document.getElementById('gpt-out-status-json').textContent = JSON.stringify(value || {}, null, 2);
  const diagnosis = value?.diagnosis || {};
  const diagnosisEl = document.getElementById('gpt-out-diagnosis');
  diagnosisEl.textContent = diagnosis.message || (active ? '捕获中。' : '尚未测试。');
  const good = ['signal_ok', 'completed_signal_ok'].includes(diagnosis.code);
  const bad = ['error', 'silent_wrong_route'].includes(diagnosis.code);
  diagnosisEl.className = `diagnosis ${good ? 'good' : bad ? 'bad' : 'warn'}`;
}

async function startGptOutTest() {
  const seconds = Number(document.getElementById('gpt-out-seconds').value || 10);
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.gpt_out.start', {
    duration_seconds: seconds,
    save_wav: document.getElementById('gpt-out-save-wav').checked,
    auto_stop: true,
  }, 15);
  renderGptOutStatus(data);
  toast(`GPT_OUT 测试已开始，${seconds} 秒后自动停止。现在让 ChatGPT Live 说话。`);
}

async function stopGptOutTest() {
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.gpt_out.stop', {}, 15);
  renderGptOutStatus(data);
  toast(data.wav_path ? `测试已停止：${data.wav_path}` : 'GPT_OUT 测试已停止');
}

async function testGptIn() {
  const data = await sendBridgeCommand(selectedAudioBridge(), 'audio.gpt_in.test', {
    duration_seconds: 2,
    frequency_hz: 660,
    volume: 0.18,
  }, 15);
  document.getElementById('gpt-in-test-json').textContent = JSON.stringify(data, null, 2);
  toast(`测试音已送入 GPT_IN；ChatGPT 麦克风请选择：${data.microphone_hint || '匹配虚拟麦克风'}`);
}

async function pollGptOutStatus() {
  const panel = document.getElementById('tab-audio');
  const bridgeId = document.getElementById('audio-bridge').value;
  if (!panel.classList.contains('active') || !bridgeId || state.audioPollInFlight) return;
  state.audioPollInFlight = true;
  try {
    const data = await sendBridgeCommand(bridgeId, 'audio.gpt_out.status', {}, 8);
    renderGptOutStatus(data);
  } catch (_) {
  } finally {
    state.audioPollInFlight = false;
  }
}

window.testProvider = async id => {
  try {
    const value = await api(`/api/providers/${id}/test`, { method: 'POST' });
    toast(JSON.stringify(value));
    await loadLogs();
  } catch (error) { toast(error.message, true); }
};

window.toggleProvider = async (id, enabled) => {
  try {
    await api(`/api/providers/${id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled }),
    });
    await loadProviders();
  } catch (error) { toast(error.message, true); }
};

window.stopSession = async id => {
  try {
    await api(`/api/sessions/${id}/stop`, { method: 'POST' });
    await Promise.all([loadSessions(), loadDashboard(), loadLogs()]);
  } catch (error) { toast(error.message, true); }
};

window.bridgeCommand = async (id, commandType) => {
  try {
    const value = await sendBridgeCommand(id, commandType);
    toast(JSON.stringify(value));
  } catch (error) { toast(error.message, true); }
};

document.querySelectorAll('.tabs button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  button.classList.add('active');
  document.getElementById(`tab-${button.dataset.tab}`).classList.add('active');
  if (button.dataset.tab === 'audio') pollGptOutStatus();
}));

document.querySelectorAll('[data-refresh]').forEach(button => button.addEventListener('click', () => {
  const loaders = { providers: loadProviders, sessions: loadSessions, bridges: loadBridges, logs: loadLogs };
  loaders[button.dataset.refresh]().catch(error => toast(error.message, true));
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
  } catch (error) { toast(error.message, true); }
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
  } catch (error) { toast(error.message, true); }
});

const clickHandlers = {
  'route-scan': scanRoutes,
  'route-auto': autoConfigureRoutes,
  'route-refresh': refreshRoutes,
  'route-save': saveRoutes,
  'gpt-out-start': startGptOutTest,
  'gpt-out-stop': stopGptOutTest,
  'gpt-in-test': testGptIn,
};
Object.entries(clickHandlers).forEach(([id, handler]) => {
  document.getElementById(id).addEventListener('click', () => {
    handler().catch(error => toast(error.message, true));
  });
});

document.getElementById('gpt-out-device').addEventListener('change', updateRouteHints);
document.getElementById('gpt-in-device').addEventListener('change', updateRouteHints);
document.getElementById('audio-bridge').addEventListener('change', () => {
  state.audioScan = null;
  state.routeStatus = null;
  document.getElementById('gpt-out-device').innerHTML = '<option value="">请先扫描</option>';
  document.getElementById('gpt-in-device').innerHTML = '<option value="">请先扫描</option>';
  renderGptOutStatus({});
});

document.getElementById('admin-token').value = localStorage.getItem('aliverAdminToken') || '';
document.getElementById('save-token').addEventListener('click', () => {
  localStorage.setItem('aliverAdminToken', document.getElementById('admin-token').value.trim());
  toast('令牌已保存到当前浏览器');
  refreshAll();
});

refreshAll();
setInterval(() => Promise.all([loadDashboard(), loadBridges()]).catch(() => {}), 8000);
setInterval(pollGptOutStatus, 1000);
