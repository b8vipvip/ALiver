(() => {
  const LABELS = {
    idle: '待机',
    talking: '说话',
    thinking: '思考',
    wave: '问候',
    happy: '开心',
    surprised: '惊讶',
    reset: '恢复',
  };
  let loading = false;
  let lastPayload = null;

  function panelActive() {
    return document.getElementById('tab-simli-tuning')?.classList.contains('active');
  }

  function unwrap(payload) {
    const raw = payload?.status;
    const bridgeData = raw?.data || raw;
    const statusEnvelope = bridgeData?.status || bridgeData;
    const runtime = statusEnvelope?.runtime || bridgeData?.runtime || null;
    const router = statusEnvelope?.status
      || runtime?.motion?.action_router
      || runtime?.action_router
      || null;
    return { runtime, router, activeSession: payload?.active_session || null };
  }

  function ensurePanel() {
    const vtubePanel = document.getElementById('vtube-studio-debug-panel');
    if (!vtubePanel || document.getElementById('avatar-action-runtime-panel')) return;
    const article = document.createElement('article');
    article.id = 'avatar-action-runtime-panel';
    article.className = 'panel avatar-action-runtime-panel';
    article.innerHTML = `
      <div class="section-title">
        <div>
          <h2>直播动作运行状态</h2>
          <p class="hint">统一显示导演动作、直播事件、ChatGPT 生成状态、GPT_OUT 语音状态、优先级队列和超时恢复。</p>
        </div>
        <div class="actions">
          <span id="avatar-action-router-badge" class="badge warn">等待状态</span>
          <button id="avatar-action-clear" type="button" class="secondary">清空动作队列</button>
        </div>
      </div>
      <div class="avatar-action-status-grid">
        <article><span>人物状态</span><strong id="avatar-action-current">未加载</strong><small id="avatar-action-current-detail">等待活动会话</small></article>
        <article><span>动作来源</span><strong id="avatar-action-source">—</strong><small id="avatar-action-priority">优先级 —</small></article>
        <article><span>剩余时间</span><strong id="avatar-action-remaining">—</strong><small id="avatar-action-restore">下一状态 —</small></article>
        <article><span>动作队列</span><strong id="avatar-action-queue-count">0</strong><small id="avatar-action-speech">GPT_OUT 未知</small></article>
      </div>
      <div class="grid two avatar-action-runtime-grid">
        <section>
          <h3>快速导演动作</h3>
          <p class="hint">手工动作使用最高优先级，可打断自动动作；结束后自动恢复说话或待机。</p>
          <div class="actions">
            <button type="button" class="secondary" data-avatar-route="thinking">思考</button>
            <button type="button" class="secondary" data-avatar-route="wave">问候</button>
            <button type="button" class="secondary" data-avatar-route="happy">开心</button>
            <button type="button" class="secondary" data-avatar-route="surprised">惊讶</button>
            <button type="button" class="secondary" data-avatar-route="reset">恢复</button>
          </div>
          <div id="avatar-action-last-result" class="diagnosis warn">尚未发送动作。</div>
        </section>
        <section>
          <h3>队列与最近事件</h3>
          <div id="avatar-action-queue" class="avatar-action-list"><p class="hint">队列为空。</p></div>
          <details>
            <summary>最近动作事件</summary>
            <div id="avatar-action-history" class="avatar-action-list"><p class="hint">暂无事件。</p></div>
          </details>
        </section>
      </div>
    `;
    const wizard = document.getElementById('vtube-motion-wizard');
    if (wizard?.parentElement) wizard.insertAdjacentElement('afterend', article);
    else vtubePanel.appendChild(article);

    article.querySelector('#avatar-action-clear')?.addEventListener('click', () => {
      clearQueue().catch(error => toast(error.message, true));
    });
    article.querySelectorAll('[data-avatar-route]').forEach(button => {
      button.addEventListener('click', () => {
        routeAction(button.dataset.avatarRoute, 'manual.debug', 100, true)
          .catch(error => toast(error.message, true));
      });
    });
  }

  function formatSource(source, router) {
    if (source) return source;
    if (router?.speaking) return 'gpt_out.speech';
    return 'natural.motion';
  }

  function eventText(item) {
    const action = LABELS[item.action] || item.action || item.event || '事件';
    const source = item.source ? ` · ${item.source}` : '';
    const reason = item.reason ? ` · ${item.reason}` : '';
    return `${action}${source}${reason}`;
  }

  function render(payload) {
    lastPayload = payload;
    ensurePanel();
    const box = document.getElementById('avatar-action-runtime-panel');
    if (!box) return;
    const { router, activeSession } = unwrap(payload);
    const badge = document.getElementById('avatar-action-router-badge');
    if (!router) {
      badge.textContent = activeSession ? '等待 Bridge' : '无活动会话';
      badge.className = 'badge warn';
      document.getElementById('avatar-action-current').textContent = '未加载';
      document.getElementById('avatar-action-current-detail').textContent = activeSession?.id || '请先启动 VTube Studio 会话';
      return;
    }

    const active = router.active;
    const state = active?.action || router.base_mode || router.next_state || 'idle';
    const restoreState = active ? (router.base_mode || 'idle') : (router.next_state || router.base_mode || 'idle');
    const source = formatSource(active?.source, router);
    const remaining = Number(router.remaining_ms || 0);
    badge.textContent = '路由器运行中';
    badge.className = 'badge ok';
    document.getElementById('avatar-action-current').textContent = LABELS[state] || state;
    document.getElementById('avatar-action-current-detail').textContent = active
      ? `动作 ${active.request_id?.slice(0, 8) || ''}`
      : '自然动作基础状态';
    document.getElementById('avatar-action-source').textContent = source;
    document.getElementById('avatar-action-priority').textContent = active
      ? `优先级 ${active.priority}`
      : router.speaking ? 'GPT_OUT 语音驱动' : '自然待机';
    document.getElementById('avatar-action-remaining').textContent = active
      ? `${(remaining / 1000).toFixed(1)} 秒`
      : '持续';
    document.getElementById('avatar-action-restore').textContent = `动作结束恢复：${LABELS[restoreState] || restoreState}`;
    document.getElementById('avatar-action-queue-count').textContent = String(router.queue_count || 0);
    document.getElementById('avatar-action-speech').textContent = router.speaking
      ? 'GPT_OUT：正在说话'
      : 'GPT_OUT：静音';

    const queue = Array.isArray(router.queue) ? router.queue : [];
    document.getElementById('avatar-action-queue').innerHTML = queue.length
      ? queue.map(item => `
          <div class="avatar-action-row">
            <strong>${escapeHtml(LABELS[item.action] || item.action)}</strong>
            <span>${escapeHtml(item.source || '')}</span>
            <code>P${Number(item.priority || 0)} · ${Number(item.age_ms || 0)}ms</code>
          </div>`).join('')
      : '<p class="hint">队列为空。</p>';

    const history = Array.isArray(router.history) ? [...router.history].reverse() : [];
    document.getElementById('avatar-action-history').innerHTML = history.length
      ? history.slice(0, 10).map(item => `
          <div class="avatar-action-row">
            <strong>${escapeHtml(item.event || 'event')}</strong>
            <span>${escapeHtml(eventText(item))}</span>
          </div>`).join('')
      : '<p class="hint">暂无事件。</p>';

    enableProceduralButtons(router);
  }

  function enableProceduralButtons(router) {
    const supported = new Set(
      lastPayload?.status?.data?.runtime?.motion?.supported_actions
      || lastPayload?.status?.runtime?.motion?.supported_actions
      || ['idle', 'talking', 'thinking', 'wave', 'happy', 'surprised', 'reset'],
    );
    document.querySelectorAll('[data-vtube-action]').forEach(button => {
      const action = button.dataset.vtubeAction;
      if (!supported.has(action)) return;
      button.disabled = false;
      button.title = '通过 ALiver 动作优先级队列触发';
    });
  }

  async function routeAction(action, source = 'manual.debug', priority = 100, force = true) {
    const durations = { thinking: 4200, wave: 2600, happy: 2800, surprised: 1800, reset: 0 };
    const result = await api('/api/avatar-actions/route', {
      method: 'POST',
      body: JSON.stringify({
        action,
        source,
        priority,
        duration_ms: durations[action] ?? 2500,
        interrupt: true,
        force,
        metadata: { ui: 'avatar_action_runtime' },
      }),
    });
    const diagnosis = document.getElementById('avatar-action-last-result');
    if (diagnosis) {
      diagnosis.className = `diagnosis ${result.routed === false ? 'warn' : 'good'}`;
      diagnosis.textContent = result.routed === false
        ? `动作未路由：${result.reason || '未知原因'}`
        : `已路由动作：${LABELS[action] || action}`;
    }
    await loadStatus();
    return result;
  }

  async function clearQueue() {
    const result = await api('/api/avatar-actions/clear', {
      method: 'POST',
      body: JSON.stringify({ include_active: true }),
    });
    toast(result.cleared ? '动作队列已清空，人物将恢复当前基础状态' : '当前没有可清理的动作');
    await loadStatus();
  }

  async function loadStatus() {
    if (loading || !panelActive()) return;
    loading = true;
    try {
      render(await api('/api/avatar-actions/status'));
    } finally {
      loading = false;
    }
  }

  // Replace the old direct-action handler with the priority router for semantic buttons.
  document.addEventListener('click', event => {
    const button = event.target?.closest?.('[data-vtube-action]');
    if (!button || button.disabled) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    routeAction(button.dataset.vtubeAction, 'manual.debug', 100, true)
      .catch(error => toast(error.message, true));
  }, true);

  function start() {
    ensurePanel();
    loadStatus().catch(() => {});
    window.setInterval(() => {
      ensurePanel();
      if (panelActive()) loadStatus().catch(() => {});
    }, 750);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
