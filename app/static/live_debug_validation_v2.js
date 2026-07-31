(() => {
  let busy = false;
  let lastPath = '';
  let lastResults = { preflight: null, simulation: null, live: null };

  function escape(value) {
    return typeof escapeHtml === 'function'
      ? escapeHtml(String(value ?? ''))
      : String(value ?? '').replace(/[&<>"']/g, char => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[char]);
  }

  function activeSession() {
    const active = new Set(['starting', 'active', 'running', 'ready', 'awaiting_manual', 'reconnecting']);
    return (state.sessions || []).find(item => active.has(item.status)) || null;
  }

  function bridgeId() {
    return document.getElementById('douyin-visible-bridge')?.value
      || activeSession()?.bridge_id
      || (state.bridges || []).find(item => item.connected)?.id
      || '';
  }

  function extensionId() {
    return (typeof selectedAutoDirectorExtension === 'function' && selectedAutoDirectorExtension())
      || document.getElementById('auto-director-extension')?.value
      || '';
  }

  function ensureStyle() {
    if (document.getElementById('aliver-live-validation-v2-style')) return;
    const style = document.createElement('style');
    style.id = 'aliver-live-validation-v2-style';
    style.textContent = `
      .validation-phase-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; margin-top:14px; }
      .validation-phase-card { border:1px solid var(--border,#263241); border-radius:12px; padding:14px; min-width:0; }
      .validation-phase-card h3 { margin:0 0 6px; }
      .validation-phase-card .actions { margin-top:12px; flex-wrap:wrap; }
      .validation-option { display:flex; align-items:center; gap:8px; margin-top:10px; color:var(--muted,#94a3b8); }
      .validation-option input { width:auto; }
      .validation-result-section { margin-top:14px; }
      .validation-level-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:10px; margin-top:10px; }
      .validation-level-grid article { border:1px solid var(--border,#263241); border-radius:9px; padding:10px; min-width:0; }
      .validation-level-grid article.passed { border-color:rgba(34,197,94,.55); }
      .validation-level-grid article.warning, .validation-level-grid article.waiting { border-color:rgba(245,158,11,.65); }
      .validation-level-grid article.failed { border-color:rgba(239,68,68,.7); }
      .validation-level-grid article.skipped { opacity:.72; }
      .validation-level-grid span,.validation-level-grid small { display:block; color:var(--muted,#94a3b8); }
      .validation-level-grid strong { display:block; margin:4px 0; overflow-wrap:anywhere; }
      .validation-summary-row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
      .validation-path { display:flex; gap:10px; align-items:center; margin-top:12px; }
      .validation-path code { flex:1; overflow-wrap:anywhere; }
    `;
    document.head.appendChild(style);
  }

  function ensureWorkspace() {
    const bar = document.querySelector('#tab-simli-tuning .live-debug-command-bar');
    if (!bar) return null;
    if (document.getElementById('live-debug-staged-validation')) return bar;

    document.getElementById('live-debug-full-validation')?.remove();
    const title = bar.querySelector('.section-title');
    if (title) {
      title.innerHTML = `
        <div>
          <h2>分阶段直播验证</h2>
          <p class="hint">开播前验证设备、窗口、数字人、音频和模拟导演闭环；开播后只验证必须依赖真实直播间互动的链路。</p>
        </div>
        <button id="live-debug-refresh-all" type="button" class="secondary">刷新全部状态</button>
      `;
    }

    const placeholder = document.getElementById('live-debug-validation-placeholder');
    if (placeholder) {
      placeholder.id = 'live-debug-staged-validation';
      placeholder.innerHTML = `
        <div class="validation-phase-grid">
          <section class="validation-phase-card">
            <span class="page-kicker">BEFORE LIVE</span>
            <h3>开播前检查</h3>
            <p class="hint">检查窗口权限、Electron 文本树、三级采集、截图、双虚拟声卡、VTube Studio 模型与动作。默认静音，不播放嗡嗡测试音。</p>
            <label class="validation-option"><input id="preflight-audible-mouth" type="checkbox">包含约 3 秒可听口型测试音</label>
            <div class="actions">
              <button id="live-debug-preflight" type="button">开播前一键检查</button>
              <button id="live-debug-simulate-welcome" type="button" class="secondary">模拟观众进入与欢迎闭环</button>
            </div>
          </section>
          <section class="validation-phase-card">
            <span class="page-kicker">AFTER LIVE</span>
            <h3>开播后实况验证</h3>
            <p class="hint">开始后请用另一账号进入直播间或发一条评论。系统等待真实事件，验证采集器、服务端事件和自动导演决策。</p>
            <div class="actions">
              <button id="live-debug-live-test" type="button">等待真实互动并验证</button>
              <button id="live-debug-open-validation" type="button" class="secondary" disabled>打开最新诊断包</button>
            </div>
          </section>
        </div>
        <div id="live-debug-v2-status" class="diagnosis warn">尚未运行分阶段验证。</div>
        <div id="live-debug-v2-results"></div>
      `;
    }

    document.getElementById('live-debug-preflight')?.addEventListener('click', () => runPreflight().catch(showError));
    document.getElementById('live-debug-simulate-welcome')?.addEventListener('click', () => simulateWelcome().catch(showError));
    document.getElementById('live-debug-live-test')?.addEventListener('click', () => runLiveValidation().catch(showError));
    document.getElementById('live-debug-open-validation')?.addEventListener('click', () => openFolder().catch(showError));
    return bar;
  }

  function stepLabel(name) {
    const labels = {
      'preflight.window_permissions': '直播伴侣窗口与权限',
      'preflight.electron_accessibility': 'Electron 无障碍文本树',
      'preflight.three_channel_probe': '三级互动采集能力',
      'preflight.window_capture': '窗口画面与 OCR 区域',
      'preflight.uia_tree': 'UIA 互动控件树',
      'preflight.collector_diagnostics': '采集诊断包',
      'preflight.audio_routes': 'GPT_IN / GPT_OUT 双虚拟声卡',
      'preflight.live_audio_lipsync': '直播语音与口型状态',
      'preflight.avatar.connection_model': 'VTube Studio 连接与模型',
      'preflight.avatar.motion_capabilities': '数字人动作参数能力',
      'preflight.avatar.actions': '数字人动作连续校验',
      'preflight.avatar.mouth_audio_route': '可听口型音频测试',
      'preflight.audible_mouth_test': '可听口型测试音',
      'live.collector_event': '真实互动识别',
      'live.server_forward': 'Bridge 转发到服务端',
      'simulation.visible_ingest': '模拟观众进入事件',
      'simulation.director_process': '自动导演欢迎决策',
      'simulation.command_dispatch': '欢迎指令发送到 ChatGPT',
      'live.server_event': '服务端互动事件',
      'live.director_decision': '自动导演真实互动决策',
    };
    return labels[name] || name || '未知检查';
  }

  function normalizedSteps(result) {
    if (Array.isArray(result?.report?.steps)) return result.report.steps;
    return [
      ...(result?.report?.collector_steps || []),
      ...(result?.report?.avatar?.steps || []),
    ].map(item => ({ ...item, level: item.ok ? 'passed' : 'failed', message: item.error || '' }));
  }

  function resultSection(title, result) {
    if (!result) return '';
    const steps = normalizedSteps(result);
    const summary = result.summary || result.report?.summary || {};
    const overall = summary.overall || (Number(summary.failed || 0) ? 'failed' : 'passed');
    const diagnosisClass = overall === 'passed' ? 'good' : overall === 'failed' ? 'bad' : 'warn';
    return `
      <section class="validation-result-section">
        <div class="section-title">
          <h3>${escape(title)}</h3>
          <div class="validation-summary-row">
            <span class="badge ${diagnosisClass}">${escape(overall)}</span>
            <span class="hint">通过 ${Number(summary.passed ?? summary.passed_count ?? 0)} · 警告 ${Number(summary.warning || 0)} · 失败 ${Number(summary.failed ?? summary.failed_count ?? 0)}</span>
          </div>
        </div>
        <div class="validation-level-grid">
          ${steps.map(item => {
            const level = String(item.level || item.status || (item.ok ? 'passed' : 'failed'));
            return `<article class="${escape(level)}">
              <span>${escape(stepLabel(item.name))}</span>
              <strong>${escape({ passed:'通过', warning:'警告', failed:'失败', waiting:'等待', skipped:'跳过' }[level] || level)}</strong>
              <small>${escape(item.message || item.error || `${Number(item.elapsed_ms || 0)} ms`)}</small>
            </article>`;
          }).join('') || '<p class="hint">没有返回检查步骤。</p>'}
        </div>
        ${result.path ? `<div class="validation-path"><code>${escape(result.path)}</code></div>` : ''}
        <details><summary>查看完整 JSON</summary><pre>${escape(JSON.stringify(result, null, 2))}</pre></details>
      </section>`;
  }

  function renderAll() {
    const target = document.getElementById('live-debug-v2-results');
    if (!target) return;
    target.innerHTML = [
      resultSection('开播前设备与本地链路', lastResults.preflight),
      resultSection('开播前模拟观众欢迎闭环', lastResults.simulation),
      resultSection('开播后真实互动闭环', lastResults.live),
    ].join('') || '<p class="hint">尚无验证结果。</p>';
    const open = document.getElementById('live-debug-open-validation');
    if (open) open.disabled = !lastPath;
  }

  function setStatus(message, level = 'warn') {
    const status = document.getElementById('live-debug-v2-status');
    if (!status) return;
    status.textContent = message;
    status.className = `diagnosis ${level}`;
  }

  function showError(error) {
    setStatus(error.message || String(error), 'bad');
    if (typeof toast === 'function') toast(error.message || String(error), true);
  }

  async function withBusy(buttonId, text, callback) {
    if (busy) throw new Error('已有验证任务正在运行，请等待完成');
    busy = true;
    const button = document.getElementById(buttonId);
    const previous = button?.textContent || '';
    if (button) { button.disabled = true; button.textContent = text; }
    try { return await callback(); }
    finally {
      busy = false;
      if (button) { button.disabled = false; button.textContent = previous; }
    }
  }

  async function runPreflight() {
    return withBusy('live-debug-preflight', '正在检查…', async () => {
      const id = bridgeId();
      if (!id) throw new Error('没有在线 Windows Bridge');
      setStatus('正在执行开播前检查。默认不会播放测试音。', 'warn');
      const result = await sendBridgeCommand(id, 'aliver.preflight_validation', {
        session_id: activeSession()?.id || null,
        test_actions: true,
        audible_mouth_test: Boolean(document.getElementById('preflight-audible-mouth')?.checked),
      }, 240);
      lastResults.preflight = result;
      lastPath = result.path || lastPath;
      renderAll();
      const overall = result.summary?.overall;
      setStatus(
        overall === 'passed' ? '开播前检查全部通过。' : overall === 'failed' ? '开播前检查存在失败项，请先修复红色项目。' : '开播前检查完成；黄色项目需要注意，但不一定阻止开播。',
        overall === 'passed' ? 'good' : overall === 'failed' ? 'bad' : 'warn',
      );
      return result;
    });
  }

  function localStep(name, level, message, data = null) {
    return { name, phase: 'simulation', level, status: level, ok: level !== 'failed', message, data };
  }

  function localResult(phase, steps, extra = {}) {
    const counts = { passed:0, warning:0, failed:0, skipped:0, waiting:0 };
    steps.forEach(step => { counts[step.level] = (counts[step.level] || 0) + 1; });
    const overall = counts.failed ? 'failed' : counts.warning || counts.waiting ? 'warning' : 'passed';
    return { completed:true, phase, summary:{ ...counts, total:steps.length, overall }, report:{ phase, steps }, ...extra };
  }

  async function simulateWelcome() {
    return withBusy('live-debug-simulate-welcome', '正在跑通欢迎闭环…', async () => {
      const ext = extensionId();
      if (!ext) throw new Error('请先在导演中心选择并保存 Chrome 导演扩展');
      setStatus('正在模拟一位新观众进入，并要求自动导演生成欢迎指令。此测试可能让 ChatGPT 说出一条测试欢迎语。', 'warn');
      const stamp = Date.now();
      const nickname = `开播前测试观众${String(stamp).slice(-4)}`;
      const ingest = await api('/api/douyin-live/simulate', {
        method: 'POST',
        body: JSON.stringify({
          extension_id: ext,
          collector_id: 'aliver-preflight-welcome',
          events: [{
            event_id: `aliver-preflight-join-${stamp}`,
            event_type: 'system',
            user_name: nickname,
            content: '进入了直播间',
            source: 'preflight_simulation',
            confidence: 1.0,
            raw_text: `${nickname} 进入了直播间`,
            observed_at: new Date().toISOString(),
          }],
          metadata: { simulated: true, validation_phase: 'preflight' },
        }),
      });
      const steps = [localStep(
        'simulation.visible_ingest',
        Number(ingest.accepted || 0) > 0 ? 'passed' : 'failed',
        Number(ingest.accepted || 0) > 0 ? '模拟进入事件已写入服务端事件队列' : `事件没有被接受：忽略 ${Number(ingest.ignored || 0)}`,
        ingest,
      )];

      let processResult = null;
      try {
        processResult = await api(`/api/auto-director/process?extension_id=${encodeURIComponent(ext)}&force=true`, { method: 'POST' });
        steps.push(localStep(
          'simulation.director_process',
          processResult.processed ? 'passed' : 'failed',
          processResult.processed ? `自动导演已处理：${processResult.action || '完成'}` : processResult.reason,
          processResult,
        ));
      } catch (error) {
        steps.push(localStep('simulation.director_process', 'failed', error.message));
      }

      const [events, decisions, status] = await Promise.all([
        api(`/api/auto-director/events?extension_id=${encodeURIComponent(ext)}&limit=30`),
        api(`/api/auto-director/decisions?extension_id=${encodeURIComponent(ext)}&limit=30`),
        api(`/api/auto-director/status?extension_id=${encodeURIComponent(ext)}`),
      ]);
      const event = (events || []).find(item => item.user_name === nickname && String(item.content || '').includes('进入'));
      const decision = event ? (decisions || []).find(item => item.event_id === event.id) : null;
      const dispatched = Boolean(decision?.command_id || event?.selected_command_id);
      steps.push(localStep(
        'simulation.command_dispatch',
        dispatched ? 'passed' : 'failed',
        dispatched
          ? `欢迎指令已生成并发送，数字人动作：${decision?.avatar_action || 'wave'}`
          : `没有找到对应欢迎命令。扩展在线=${Boolean(status.extension_connected)}，输入框就绪=${Boolean(status.composer_ready)}`,
        { event, decision, status },
      ));
      lastResults.simulation = localResult('simulation', steps);
      renderAll();
      const overall = lastResults.simulation.summary.overall;
      setStatus(
        overall === 'passed' ? '模拟观众进入 → 自动导演欢迎 → ChatGPT 命令闭环已跑通。' : '模拟欢迎闭环没有完全通过，请查看红色步骤。',
        overall === 'passed' ? 'good' : 'bad',
      );
      if (typeof loadAutoDirector === 'function') loadAutoDirector().catch(() => {});
      return lastResults.simulation;
    });
  }

  async function verifyServerAndDirector(bridgeResult) {
    const ext = extensionId();
    const steps = normalizedSteps(bridgeResult);
    if (!ext) {
      steps.push(localStep('live.server_event', 'warning', '未选择导演扩展，只完成了 Bridge 端真实互动识别'));
      return localResult('live', steps, { bridge_result: bridgeResult, path: bridgeResult.path });
    }
    const bridgeEvent = steps.find(item => item.name === 'live.collector_event')?.data || {};
    const [events, status] = await Promise.all([
      api(`/api/auto-director/events?extension_id=${encodeURIComponent(ext)}&limit=60`),
      api(`/api/auto-director/status?extension_id=${encodeURIComponent(ext)}`),
    ]);
    const serverEvent = (events || []).find(item =>
      (!bridgeEvent.user_name || item.user_name === bridgeEvent.user_name)
      && (!bridgeEvent.content || String(item.content || '').includes(String(bridgeEvent.content || '').slice(0, 40)))
    );
    steps.push(localStep(
      'live.server_event',
      serverEvent ? 'passed' : 'failed',
      serverEvent ? '服务端事件队列已收到同一条真实互动' : 'Bridge 已识别互动，但服务端事件列表中未找到对应记录',
      { serverEvent, bridgeEvent },
    ));

    if (serverEvent?.status === 'queued' && status.extension_connected && status.composer_ready && !status.generating) {
      try {
        await api(`/api/auto-director/process?extension_id=${encodeURIComponent(ext)}&force=true`, { method: 'POST' });
      } catch (_) {}
    }
    const decisions = await api(`/api/auto-director/decisions?extension_id=${encodeURIComponent(ext)}&limit=60`);
    const decision = serverEvent ? (decisions || []).find(item => item.event_id === serverEvent.id) : null;
    steps.push(localStep(
      'live.director_decision',
      decision?.command_id ? 'passed' : 'failed',
      decision?.command_id
        ? `自动导演已生成真实互动指令，动作：${decision.avatar_action || '无'}`
        : `没有生成对应导演命令。导演状态=${status.run?.status || 'unknown'}，扩展在线=${Boolean(status.extension_connected)}`,
      { decision, status },
    ));
    return localResult('live', steps, { bridge_result: bridgeResult, path: bridgeResult.path });
  }

  async function runLiveValidation() {
    return withBusy('live-debug-live-test', '正在等待真实互动…', async () => {
      const id = bridgeId();
      if (!id) throw new Error('没有在线 Windows Bridge');
      setStatus('正在等待真实互动。现在请用另一账号进入直播间或发送一条评论；最长等待 90 秒。', 'warn');
      const bridgeResult = await sendBridgeCommand(id, 'aliver.live_validation', {
        live_timeout_seconds: 90,
      }, 120);
      lastPath = bridgeResult.path || lastPath;
      lastResults.live = await verifyServerAndDirector(bridgeResult);
      renderAll();
      const overall = lastResults.live.summary.overall;
      setStatus(
        overall === 'passed'
          ? '真实互动 → Bridge 采集 → 服务端事件 → 自动导演命令闭环已通过。'
          : '实况验证未完全通过，请按黄色或红色步骤继续处理。',
        overall === 'passed' ? 'good' : overall === 'failed' ? 'bad' : 'warn',
      );
      if (typeof loadAutoDirector === 'function') loadAutoDirector().catch(() => {});
      return lastResults.live;
    });
  }

  async function openFolder() {
    const id = bridgeId();
    if (!id) throw new Error('没有在线 Windows Bridge');
    if (!lastPath) throw new Error('请先运行一次 Bridge 验证');
    await sendBridgeCommand(id, 'douyin.visible.open_diagnostics_folder', { path: lastPath }, 30);
  }

  function start() {
    ensureStyle();
    if (!ensureWorkspace() || typeof sendBridgeCommand !== 'function') {
      window.setTimeout(start, 200);
      return;
    }
    window.runAliverFullValidation = runPreflight;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
