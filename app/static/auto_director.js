const autoDirectorState = {
  extensions: [],
  config: null,
  status: null,
  run: null,
  decisions: [],
  events: [],
  loading: false,
  configDirty: false,
};

function selectedAutoDirectorExtension() {
  return document.getElementById('auto-director-extension')?.value || '';
}

function linesFrom(value) {
  return String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean);
}

function slugSegment(name, index) {
  if (/开场|暖场/.test(name)) return 'opening';
  if (/收尾|结束|告别/.test(name)) return 'closing';
  if (/互动|拉活/.test(name)) return `engagement-${index + 1}`;
  if (/主题/.test(name)) return `topic-${index + 1}`;
  return `segment-${index + 1}`;
}

function rundownFromLines(value) {
  return linesFrom(value).map((line, index) => {
    const [nameRaw, minutesRaw, objectiveRaw, actionRaw, cueRaw] = line.split('|').map(item => item.trim());
    const name = nameRaw || `环节 ${index + 1}`;
    const minutes = Math.max(0.5, Math.min(Number(minutesRaw || 5), 240));
    const avatarAction = ['idle', 'talking', 'thinking', 'wave', 'happy', 'surprised', 'reset']
      .includes(actionRaw) ? actionRaw : 'thinking';
    return {
      id: slugSegment(name, index),
      name,
      duration_seconds: Math.round(minutes * 60),
      objective: objectiveRaw || '保持自然互动和直播节奏。',
      cue: cueRaw || `围绕“${name}”自然承接话题，并邀请观众参与。`,
      avatar_action: avatarAction,
    };
  });
}

function rundownToLines(rundown) {
  return (Array.isArray(rundown) ? rundown : []).map(item => [
    item.name || '未命名环节',
    Math.max(0.5, Number(item.duration_seconds || 300) / 60),
    item.objective || '',
    item.avatar_action || 'thinking',
    item.cue || '',
  ].join('|')).join('\n');
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  const minutes = Math.floor(value / 60);
  const rest = Math.floor(value % 60);
  return minutes ? `${minutes}分${String(rest).padStart(2, '0')}秒` : `${rest}秒`;
}

function ensureProfessionalDirectorUi() {
  const intro = document.querySelector('#tab-auto-director .auto-director-intro');
  if (!intro || document.getElementById('professional-director-control')) return;
  const heading = intro.querySelector('h2');
  const hint = intro.querySelector('.hint');
  if (heading) heading.textContent = '专业单导演控制台';
  if (hint) hint.textContent = '一个总导演统一负责节目单、互动选择、主播口播、数字人动作、节奏控制与安全兜底。';

  const control = document.createElement('article');
  control.id = 'professional-director-control';
  control.className = 'panel professional-director-control';
  control.innerHTML = `
    <div class="section-title">
      <div>
        <h2>直播执导台</h2>
        <p class="hint">先保存节目配置，再点击“开始执导”。紧急停止会立即阻止自动导演继续发送新命令。</p>
      </div>
      <span id="professional-run-badge" class="badge warn">待命</span>
    </div>
    <div class="professional-run-grid">
      <article><span>当前环节</span><strong id="professional-current-segment">未开始</strong><small id="professional-segment-objective">—</small></article>
      <article><span>直播计时</span><strong id="professional-run-elapsed">0秒</strong><small id="professional-segment-elapsed">本环节 0秒</small></article>
      <article><span>下一环节</span><strong id="professional-next-segment">—</strong><small id="professional-next-cue">下次提示 —</small></article>
      <article><span>最近决策</span><strong id="professional-last-decision">—</strong><small id="professional-last-reason">尚未开始执导</small></article>
    </div>
    <div class="actions professional-run-actions">
      <button type="button" data-run-action="start">开始执导</button>
      <button type="button" data-run-action="pause" class="secondary">暂停</button>
      <button type="button" data-run-action="resume" class="secondary">继续</button>
      <button type="button" data-run-action="next_segment" class="secondary">下一环节</button>
      <button type="button" data-run-action="close" class="secondary">进入收尾</button>
      <button type="button" data-run-action="stop" class="secondary">结束执导</button>
      <button type="button" data-run-action="emergency_stop" class="danger">紧急停止</button>
    </div>
    <div id="professional-run-diagnosis" class="diagnosis warn">等待总导演开始工作。</div>
  `;
  intro.insertAdjacentElement('afterend', control);
  control.querySelectorAll('[data-run-action]').forEach(button => {
    button.addEventListener('click', () => {
      controlProfessionalRun(button.dataset.runAction).catch(error => toast(error.message, true));
    });
  });

  const form = document.getElementById('auto-director-config-form');
  const aiFields = document.getElementById('auto-director-ai-fields');
  if (form && aiFields && !document.getElementById('professional-director-fields')) {
    const fields = document.createElement('section');
    fields.id = 'professional-director-fields';
    fields.className = 'professional-director-fields';
    fields.innerHTML = `
      <div class="section-title"><h3>总导演工作简报</h3><span class="hint">这些内容决定导演如何选互动、控节奏和给主播下指令。</span></div>
      <div class="grid two">
        <label>导演名称<input name="director_name" placeholder="ALiver 总导演"></label>
        <label>直播标题<input name="show_title" placeholder="ALiver 日常聊天直播"></label>
      </div>
      <label>本场目标<textarea name="show_goal" rows="3" placeholder="例如：轻松聊天，提高有效互动和停留。"></textarea></label>
      <label>主播人设<textarea name="host_persona" rows="3" placeholder="例如：自然、亲切、机灵，不机械念稿。"></textarea></label>
      <label>目标观众<textarea name="audience_profile" rows="2" placeholder="例如：喜欢轻松聊天、AI和日常话题的观众。"></textarea></label>
      <label>导演风格<textarea name="director_style" rows="3" placeholder="少而准地下指令，不连续轰炸主播。"></textarea></label>
      <div class="grid two">
        <label>开场口播目标<textarea name="opening_script" rows="5"></textarea></label>
        <label>收尾口播目标<textarea name="closing_script" rows="5"></textarea></label>
      </div>
      <label>节目单（每行：环节名 | 分钟 | 目标 | 动作 | 导演提示）
        <textarea name="rundown_lines" rows="9" placeholder="开场与暖场|3|问好并邀请互动|wave|欢迎观众并抛出简单问题"></textarea>
      </label>
      <div class="inline-fields auto-director-numbers">
        <label>每位观众冷却（秒）<input name="per_user_cooldown_seconds" type="number" min="0" max="3600" value="120"></label>
        <label>连续回复上限<input name="max_consecutive_replies" type="number" min="1" max="20" value="4"></label>
        <label>环节提示间隔（秒）<input name="segment_cue_interval_seconds" type="number" min="20" max="1800" value="90"></label>
        <label>事件最大等待（秒）<input name="max_queue_age_seconds" type="number" min="30" max="3600" value="180"></label>
      </div>
    `;
    aiFields.insertAdjacentElement('afterend', fields);
  }

  const eventPanel = document.querySelector('#auto-director-event-list')?.closest('.panel');
  if (eventPanel && !document.getElementById('professional-decision-list')) {
    const decisions = document.createElement('article');
    decisions.className = 'panel';
    decisions.innerHTML = `
      <div class="section-title">
        <h2>总导演决策记录</h2>
        <span class="hint">记录为什么回复、忽略、等待、切换环节，以及同时下达的数字人动作。</span>
      </div>
      <div id="professional-decision-list" class="professional-decision-list"><p class="hint">暂无决策。</p></div>
    `;
    eventPanel.insertAdjacentElement('beforebegin', decisions);
  }

  document.getElementById('auto-director-config-form')?.addEventListener('input', () => {
    autoDirectorState.configDirty = true;
  });
  document.getElementById('auto-director-config-form')?.addEventListener('change', () => {
    autoDirectorState.configDirty = true;
  });
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

function fillAutoDirectorConfig(config, force = false) {
  if (autoDirectorState.configDirty && !force) return;
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
  if (form.elements.director_name) form.elements.director_name.value = settings.director_name || 'ALiver 总导演';
  if (form.elements.show_title) form.elements.show_title.value = settings.show_title || 'ALiver 日常聊天直播';
  if (form.elements.show_goal) form.elements.show_goal.value = settings.show_goal || '';
  if (form.elements.host_persona) form.elements.host_persona.value = settings.host_persona || '';
  if (form.elements.audience_profile) form.elements.audience_profile.value = settings.audience_profile || '';
  if (form.elements.director_style) form.elements.director_style.value = settings.director_style || '';
  if (form.elements.opening_script) form.elements.opening_script.value = settings.opening_script || '';
  if (form.elements.closing_script) form.elements.closing_script.value = settings.closing_script || '';
  if (form.elements.rundown_lines) form.elements.rundown_lines.value = rundownToLines(settings.rundown);
  if (form.elements.per_user_cooldown_seconds) form.elements.per_user_cooldown_seconds.value = settings.per_user_cooldown_seconds ?? 120;
  if (form.elements.max_consecutive_replies) form.elements.max_consecutive_replies.value = settings.max_consecutive_replies ?? 4;
  if (form.elements.segment_cue_interval_seconds) form.elements.segment_cue_interval_seconds.value = settings.segment_cue_interval_seconds ?? 90;
  if (form.elements.max_queue_age_seconds) form.elements.max_queue_age_seconds.value = settings.max_queue_age_seconds ?? 180;
  setAutoDirectorModeVisibility();
  autoDirectorState.configDirty = false;
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
  const runStatus = status.run?.status || 'stopped';
  badge.className = `badge ${status.enabled && ['live', 'closing'].includes(runStatus) ? 'ok' : 'warn'}`;
  badge.textContent = status.enabled ? `导演 ${runStatus}` : '已停用';
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

function renderProfessionalRun() {
  const run = autoDirectorState.run;
  const badge = document.getElementById('professional-run-badge');
  if (!badge) return;
  if (!run) {
    badge.textContent = '未创建';
    badge.className = 'badge warn';
    return;
  }
  const className = ['live', 'closing'].includes(run.status) ? 'ok' : run.status === 'emergency' ? 'bad' : 'warn';
  badge.textContent = `${run.status} · ${run.phase}`;
  badge.className = `badge ${className}`;
  document.getElementById('professional-current-segment').textContent = run.current_segment?.name || '未开始';
  document.getElementById('professional-segment-objective').textContent = run.current_segment?.objective || '—';
  document.getElementById('professional-run-elapsed').textContent = formatDuration(run.elapsed_seconds);
  document.getElementById('professional-segment-elapsed').textContent = `本环节 ${formatDuration(run.segment_elapsed_seconds)}`;
  document.getElementById('professional-next-segment').textContent = run.next_segment?.name || '无';
  document.getElementById('professional-next-cue').textContent = `下次提示：${formatTime(run.next_cue_at)}`;
  const decision = autoDirectorState.decisions[0];
  document.getElementById('professional-last-decision').textContent = decision?.decision_type || '—';
  document.getElementById('professional-last-reason').textContent = decision?.reason || run.state?.last_reason || '尚未开始执导';
  const diagnosis = document.getElementById('professional-run-diagnosis');
  diagnosis.className = `diagnosis ${className === 'ok' ? 'ok' : className === 'bad' ? 'bad' : 'warn'}`;
  diagnosis.textContent = run.status === 'live'
    ? `总导演正在执导“${run.current_segment?.name || '当前环节'}”，会在主播空闲时选择互动或补充环节提示。`
    : run.status === 'paused'
      ? '总导演已暂停，不会自动发送新命令。'
      : run.status === 'emergency'
        ? '紧急停止已生效。自动导演不会继续发送任何新命令。'
        : run.status === 'closing'
          ? '总导演正在执行收尾，完成口播后请点击“结束执导”。'
          : '总导演处于待命状态。';
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
        <button class="danger" onclick="dismissAutoDirectorEvent('${event.id}')" ${event.status === 'selected' || event.status === 'ignored' ? 'disabled' : ''}>忽略</button>
      </div>
    </div>
  `).join('') || '<p class="hint">暂无互动事件。</p>';
}

function renderProfessionalDecisions() {
  const container = document.getElementById('professional-decision-list');
  if (!container) return;
  container.innerHTML = autoDirectorState.decisions.map(decision => `
    <div class="professional-decision-card">
      <div class="item-head">
        <div>
          <strong>${escapeHtml(decision.decision_type)}</strong>
          <div class="meta">${formatTime(decision.created_at)} · P${decision.priority} · 动作 ${escapeHtml(decision.avatar_action || '无')}</div>
        </div>
        ${eventStatus(decision.command_id ? 'dispatched' : decision.decision_type)}
      </div>
      <div class="auto-event-content">${escapeHtml(decision.reason || '')}</div>
      ${decision.instruction ? `<details><summary>查看给主播的导演要求</summary><div class="command-text">${escapeHtml(decision.instruction)}</div></details>` : ''}
      <div class="meta">事件 ${escapeHtml(decision.event_id || '无')} · 命令 ${escapeHtml(decision.command_id || '无')}</div>
    </div>
  `).join('') || '<p class="hint">暂无决策。</p>';
}

async function loadAutoDirector({ refreshForm = false } = {}) {
  if (autoDirectorState.loading) return;
  autoDirectorState.loading = true;
  try {
    ensureProfessionalDirectorUi();
    autoDirectorState.extensions = await api('/api/director/extensions');
    renderAutoDirectorExtensions();
    const extensionId = selectedAutoDirectorExtension();
    if (!extensionId) {
      autoDirectorState.config = null;
      autoDirectorState.status = null;
      autoDirectorState.run = null;
      autoDirectorState.decisions = [];
      autoDirectorState.events = [];
      renderAutoDirectorStatus();
      renderProfessionalRun();
      renderProfessionalDecisions();
      renderAutoDirectorEvents();
      return;
    }
    const [config, status, events, run, decisions] = await Promise.all([
      api(`/api/auto-director/config?extension_id=${encodeURIComponent(extensionId)}`),
      api(`/api/auto-director/status?extension_id=${encodeURIComponent(extensionId)}`),
      api(`/api/auto-director/events?extension_id=${encodeURIComponent(extensionId)}&limit=100`),
      api(`/api/auto-director/run?extension_id=${encodeURIComponent(extensionId)}`),
      api(`/api/auto-director/decisions?extension_id=${encodeURIComponent(extensionId)}&limit=50`),
    ]);
    autoDirectorState.config = config;
    autoDirectorState.status = status;
    autoDirectorState.events = events;
    autoDirectorState.run = run;
    autoDirectorState.decisions = decisions;
    fillAutoDirectorConfig(config, refreshForm);
    renderAutoDirectorStatus();
    renderProfessionalRun();
    renderProfessionalDecisions();
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
  const rundown = rundownFromLines(form.get('rundown_lines'));
  if (!rundown.length) throw new Error('节目单至少需要一个环节');
  const payload = {
    extension_id: extensionId,
    enabled: form.get('enabled') === 'on',
    mode: form.get('mode'),
    api_base_url: form.get('api_base_url') || null,
    model_name: form.get('model_name') || null,
    api_key: form.get('api_key') || null,
    settings: {
      professional_mode: true,
      director_name: form.get('director_name') || 'ALiver 总导演',
      show_title: form.get('show_title') || 'ALiver 日常聊天直播',
      show_goal: form.get('show_goal') || '',
      host_persona: form.get('host_persona') || '',
      audience_profile: form.get('audience_profile') || '',
      director_style: form.get('director_style') || '',
      opening_script: form.get('opening_script') || '',
      closing_script: form.get('closing_script') || '',
      rundown,
      min_score: Number(form.get('min_score') || 35),
      cooldown_seconds: Number(form.get('cooldown_seconds') || 12),
      idle_seconds: Number(form.get('idle_seconds') || 120),
      max_response_seconds: Number(form.get('max_response_seconds') || 25),
      dedupe_window_seconds: Number(form.get('dedupe_window_seconds') || 90),
      max_comment_chars: 300,
      temperature: 0.3,
      director_temperature: 0.25,
      per_user_cooldown_seconds: Number(form.get('per_user_cooldown_seconds') || 120),
      max_consecutive_replies: Number(form.get('max_consecutive_replies') || 4),
      segment_cue_interval_seconds: Number(form.get('segment_cue_interval_seconds') || 90),
      max_queue_age_seconds: Number(form.get('max_queue_age_seconds') || 180),
      event_batch_size: 8,
      blocked_keywords: linesFrom(form.get('blocked_keywords')),
      idle_topics: linesFrom(form.get('idle_topics')),
    },
  };
  autoDirectorState.config = await api('/api/auto-director/config', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
  autoDirectorState.configDirty = false;
  toast(payload.enabled ? '专业总导演配置已保存并启用' : '专业总导演配置已保存');
  await loadAutoDirector({ refreshForm: true });
}

async function controlProfessionalRun(action) {
  const extensionId = selectedAutoDirectorExtension();
  if (!extensionId) throw new Error('请先选择 Chrome 导演扩展');
  if (action === 'emergency_stop' && !window.confirm('确认紧急停止自动导演？它将立即停止发送所有新命令。')) return;
  autoDirectorState.run = await api('/api/auto-director/run/control', {
    method: 'POST',
    body: JSON.stringify({ extension_id: extensionId, action }),
  });
  const labels = {
    start: '总导演已开始执导', pause: '总导演已暂停', resume: '总导演已继续执导',
    next_segment: '已切换到下一环节', close: '已进入收尾', stop: '总导演已结束',
    emergency_stop: '紧急停止已生效', reset: '导演状态已重置',
  };
  toast(labels[action] || '导演状态已更新', action === 'emergency_stop');
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
  toast(result.processed ? `总导演已处理：${result.action || '完成'}` : result.reason, !result.processed);
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

window.dismissAutoDirectorEvent = async id => {
  try {
    await api(`/api/auto-director/events/${id}/dismiss`, { method: 'POST' });
    toast('事件已由人工导演忽略');
    await loadAutoDirector();
  } catch (error) { toast(error.message, true); }
};

function installAutoDirectorHandlers() {
  ensureProfessionalDirectorUi();
  document.getElementById('auto-director-config-form').addEventListener('submit', event => {
    saveAutoDirectorConfig(event).catch(error => toast(error.message, true));
  });
  document.getElementById('auto-director-event-form').addEventListener('submit', event => {
    submitAutoDirectorEvent(event).catch(error => toast(error.message, true));
  });
  document.getElementById('auto-director-extension').addEventListener('change', () => {
    autoDirectorState.config = null;
    autoDirectorState.configDirty = false;
    loadAutoDirector({ refreshForm: true }).catch(error => toast(error.message, true));
  });
  document.getElementById('auto-director-mode').addEventListener('change', setAutoDirectorModeVisibility);
  document.getElementById('auto-director-refresh').addEventListener('click', () => {
    loadAutoDirector({ refreshForm: !autoDirectorState.configDirty }).catch(error => toast(error.message, true));
  });
  document.getElementById('auto-director-process').addEventListener('click', () => {
    processAutoDirectorOnce().catch(error => toast(error.message, true));
  });
}

installAutoDirectorHandlers();
loadAutoDirector({ refreshForm: true }).catch(() => {});
setInterval(() => {
  const panel = document.getElementById('tab-auto-director');
  if (panel?.classList.contains('active')) loadAutoDirector().catch(() => {});
}, 2000);
