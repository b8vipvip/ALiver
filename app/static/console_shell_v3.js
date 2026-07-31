(() => {
  const VERSION = '0.15.0';
  const ORDER = [
    ['overview', '总览'],
    ['director', '导演中心'],
    ['simli-tuning', '直播调试'],
    ['live-runs', '直播记录'],
    ['sessions', '数字人会话'],
    ['providers', '供应商'],
    ['voice', '语音实验室'],
    ['audio', '音频路由'],
    ['bridges', 'Bridge 节点'],
    ['logs', '运行日志'],
  ];
  const GROUPS = {
    overview: '直播工作台',
    sessions: '数字人与声音',
    bridges: '系统与维护',
  };

  function injectStyle() {
    if (document.getElementById('aliver-console-shell-v3-style')) return;
    const link = document.createElement('link');
    link.id = 'aliver-console-shell-v3-style';
    link.rel = 'stylesheet';
    link.href = `/static/console_shell_v3.css?v=${VERSION}`;
    document.head.appendChild(link);
  }

  function panel(id, html) {
    let element = document.getElementById(`tab-${id}`);
    if (element) return element;
    element = document.createElement('section');
    element.id = `tab-${id}`;
    element.className = 'tab-panel';
    element.innerHTML = html;
    return element;
  }

  function activate(name) {
    document.querySelectorAll('.tabs button[data-tab]').forEach(button => {
      button.classList.toggle('active', button.dataset.tab === name);
    });
    document.querySelectorAll('.tab-panel').forEach(item => item.classList.remove('active'));
    const target = document.getElementById(`tab-${name}`);
    if (target) target.classList.add('active');
    localStorage.setItem('aliverActiveWorkspace', name);
    window.dispatchEvent(new CustomEvent('aliver:tabchange', { detail: { name } }));
  }

  function overviewMarkup() {
    return `
      <header class="page-heading shell-page-heading">
        <div><span class="page-kicker">LIVE OPERATIONS</span><h2>直播工作台</h2>
        <p>用一页确认系统是否适合开播，并快速进入导演、调试、语音和直播记录。</p></div>
        <div class="actions"><button type="button" data-go="simli-tuning">运行开播前检查</button>
        <button type="button" class="secondary" data-go="director">进入导演中心</button></div>
      </header>
      <section id="shell-readiness" class="shell-readiness-grid"></section>
      <section class="shell-dashboard-grid">
        <article class="panel shell-primary-card">
          <div class="section-title"><div><span class="page-kicker">CURRENT LIVE</span><h2>当前直播状态</h2></div>
          <span id="shell-live-badge" class="badge warn">读取中</span></div>
          <div id="shell-live-summary" class="shell-live-summary"><p class="hint">正在读取直播记录与自动导演状态。</p></div>
          <div class="actions"><button type="button" data-go="live-runs">查看直播记录</button>
          <button type="button" class="secondary" data-go="voice">调整语音</button></div>
        </article>
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">QUICK PATHS</span><h2>常用入口</h2></div></div>
          <div class="shell-quick-grid">
            <button type="button" data-go="director"><strong>导演中心</strong><span>节目单、互动和口播</span></button>
            <button type="button" data-go="simli-tuning"><strong>直播调试</strong><span>采集、口型和闭环验证</span></button>
            <button type="button" data-go="voice"><strong>语音实验室</strong><span>音色、风格和 TTS</span></button>
            <button type="button" data-go="audio"><strong>音频路由</strong><span>GPT_IN / GPT_OUT</span></button>
          </div>
        </article>
      </section>
      <article class="panel shell-system-card">
        <div class="section-title"><div><span class="page-kicker">SYSTEM SNAPSHOT</span><h2>资源概览</h2></div>
        <button type="button" id="shell-refresh" class="secondary">刷新</button></div>
        <div id="shell-metrics-host"></div>
      </article>`;
  }

  function buildShell() {
    if (document.getElementById('aliver-app-shell')) return true;
    const main = document.querySelector('main');
    const tabs = document.querySelector('nav.tabs');
    if (!main || !tabs) return false;

    const overview = panel('overview', overviewMarkup());
    const liveRuns = panel('live-runs', '<div id="live-run-console-root"></div>');
    const voice = panel('voice', '<div id="voice-lab-root"></div>');
    main.append(overview, liveRuns, voice);

    const shell = document.createElement('div');
    shell.id = 'aliver-app-shell';
    shell.className = 'aliver-app-shell';
    const sidebar = document.createElement('aside');
    sidebar.className = 'aliver-sidebar';
    sidebar.innerHTML = `
      <div class="sidebar-head"><div><strong>ALiver</strong><span>Live Control</span></div>
      <button id="sidebar-collapse" type="button" class="secondary" aria-label="折叠菜单">☰</button></div>`;
    const workspace = document.createElement('div');
    workspace.className = 'aliver-workspace';
    const status = document.createElement('div');
    status.id = 'shell-status-ribbon';
    status.className = 'shell-status-ribbon';
    status.innerHTML = '<span>系统状态读取中…</span>';
    workspace.appendChild(status);

    tabs.querySelector('[data-tab="auto-director"]')?.remove();
    const buttons = new Map([...tabs.querySelectorAll('button[data-tab]')].map(button => [button.dataset.tab, button]));
    for (const [name, label] of ORDER) {
      let button = buttons.get(name);
      if (!button) {
        button = document.createElement('button');
        button.type = 'button';
        button.dataset.tab = name;
      }
      button.textContent = label;
      buttons.set(name, button);
    }
    tabs.replaceChildren();
    for (const [name] of ORDER) {
      if (GROUPS[name]) {
        const label = document.createElement('span');
        label.className = 'sidebar-group-label';
        label.textContent = GROUPS[name];
        tabs.appendChild(label);
      }
      tabs.appendChild(buttons.get(name));
    }
    sidebar.appendChild(tabs);

    const panels = [...main.querySelectorAll(':scope > .tab-panel')];
    for (const item of panels) workspace.appendChild(item);
    shell.append(sidebar, workspace);
    main.appendChild(shell);

    const metrics = document.getElementById('metrics');
    const metricsHost = document.getElementById('shell-metrics-host');
    if (metrics && metricsHost) metricsHost.appendChild(metrics);

    tabs.addEventListener('click', event => {
      const button = event.target.closest('button[data-tab]');
      if (!button) return;
      event.preventDefault();
      activate(button.dataset.tab);
    });
    document.body.addEventListener('click', event => {
      const button = event.target.closest('[data-go]');
      if (button) activate(button.dataset.go);
    });
    document.getElementById('sidebar-collapse')?.addEventListener('click', () => {
      document.body.classList.toggle('sidebar-collapsed');
      localStorage.setItem('aliverSidebarCollapsed', document.body.classList.contains('sidebar-collapsed') ? '1' : '0');
    });
    if (localStorage.getItem('aliverSidebarCollapsed') === '1') document.body.classList.add('sidebar-collapsed');
    document.getElementById('shell-refresh')?.addEventListener('click', () => refreshOverview(true));

    const remembered = localStorage.getItem('aliverActiveWorkspace');
    activate(ORDER.some(([name]) => name === remembered) ? remembered : 'overview');
    return true;
  }

  function readinessCard(title, value, detail, good) {
    return `<article class="shell-readiness-card ${good ? 'is-good' : 'is-warn'}">
      <span>${title}</span><strong>${value}</strong><small>${detail}</small></article>`;
  }

  let refreshBusy = false;
  async function refreshOverview(showToast = false) {
    if (refreshBusy || !document.getElementById('shell-readiness')) return;
    refreshBusy = true;
    try {
      const [health, dashboard, bridges, extensions, liveRun] = await Promise.all([
        api('/api/health'),
        api('/api/dashboard'),
        api('/api/bridges'),
        api('/api/director/extensions'),
        api('/api/live-runs/status'),
      ]);
      const onlineBridges = bridges.filter(item => item.connected);
      const onlineExtensions = extensions.filter(item => item.connected);
      const activeSessions = Number(dashboard.active_sessions || 0);
      document.getElementById('shell-readiness').innerHTML = [
        readinessCard('服务端', health.version || '未知', health.status || 'unknown', health.status === 'ok'),
        readinessCard('Windows Bridge', `${onlineBridges.length} 在线`, onlineBridges[0]?.version || '未连接', onlineBridges.length > 0),
        readinessCard('导演扩展', `${onlineExtensions.length} 在线`, onlineExtensions[0]?.version || '未连接', onlineExtensions.length > 0),
        readinessCard('数字人会话', `${activeSessions} 活动`, activeSessions ? '可执行动作与口型' : '尚未启动', activeSessions > 0),
      ].join('');
      const badge = document.getElementById('shell-live-badge');
      badge.textContent = liveRun.active ? '记录中' : '未记录';
      badge.className = `badge ${liveRun.active ? 'good' : 'warn'}`;
      document.getElementById('shell-live-summary').innerHTML = liveRun.active
        ? `<div class="shell-live-stat"><strong>${Math.round(liveRun.duration_seconds || 0)} 秒</strong><span>持续时间</span></div>
           <div class="shell-live-stat"><strong>${liveRun.record_count || 0}</strong><span>记录条目</span></div>
           <p class="meta">${escapeHtml(liveRun.title || liveRun.run_id || '')}</p>`
        : '<p class="hint">自动导演进入直播状态后可自动开始记录，也可在“直播记录”中手动启动。</p>';
      document.getElementById('shell-status-ribbon').innerHTML = `
        <span class="${health.status === 'ok' ? 'good-dot' : 'bad-dot'}">服务端 ${escapeHtml(health.version || '')}</span>
        <span class="${onlineBridges.length ? 'good-dot' : 'warn-dot'}">Bridge ${onlineBridges.length ? '在线' : '离线'}</span>
        <span class="${onlineExtensions.length ? 'good-dot' : 'warn-dot'}">导演扩展 ${onlineExtensions.length ? '在线' : '离线'}</span>
        <span class="${liveRun.active ? 'live-dot' : 'muted-dot'}">直播记录 ${liveRun.active ? '运行中' : '待机'}</span>`;
      if (showToast) toast('工作台状态已刷新');
    } catch (error) {
      document.getElementById('shell-status-ribbon').innerHTML = `<span class="bad-dot">状态读取失败：${escapeHtml(error.message)}</span>`;
      if (showToast) toast(error.message, true);
    } finally {
      refreshBusy = false;
    }
  }

  function start() {
    injectStyle();
    if (!buildShell()) {
      const observer = new MutationObserver(() => {
        if (buildShell()) observer.disconnect();
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    setTimeout(() => refreshOverview(), 800);
    setInterval(() => refreshOverview(), 5000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
