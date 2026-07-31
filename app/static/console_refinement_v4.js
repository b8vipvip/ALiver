(() => {
  const VERSION = '0.15.1-hotfix2';
  const STABILIZE_DELAYS = [0, 80, 220, 600, 1400];

  function injectStyle() {
    if (document.getElementById('aliver-console-refinement-v4-style')) return;
    const link = document.createElement('link');
    link.id = 'aliver-console-refinement-v4-style';
    link.rel = 'stylesheet';
    link.href = `/static/console_refinement_v4.css?v=${VERSION}`;
    document.head.appendChild(link);
  }

  function activateSessionSubpanel(name) {
    const sessionPanel = document.getElementById('avatar-session-main');
    const providerPanel = document.getElementById('avatar-provider-subpanel');
    if (!sessionPanel || !providerPanel) return;
    sessionPanel.hidden = name !== 'sessions';
    providerPanel.hidden = name !== 'providers';
    document.querySelectorAll('.avatar-session-switcher button').forEach(button => {
      button.classList.toggle('active', button.dataset.sessionView === name);
      button.setAttribute('aria-selected', button.dataset.sessionView === name ? 'true' : 'false');
    });
    localStorage.setItem('aliverAvatarWorkspace', name);
  }

  function createSessionHeading() {
    const heading = document.createElement('header');
    heading.className = 'page-heading';
    heading.dataset.aliverSessionHeading = '1';
    heading.innerHTML = `
      <div>
        <span class="page-kicker">SESSIONS</span>
        <h2>数字人会话</h2>
        <p>启动、命名、停止、重启和管理本地或云端数字人会话。</p>
      </div>`;
    return heading;
  }

  function normalizeSessionHeading(sessions) {
    const direct = [...sessions.children].filter(item => item.classList?.contains('page-heading'));
    const nested = [...sessions.querySelectorAll(':scope > #avatar-session-main > .page-heading')];
    const headings = [...direct, ...nested];
    const keeper = direct[0] || nested[0] || createSessionHeading();
    headings.forEach(item => {
      if (item !== keeper) item.remove();
    });
    if (keeper.parentElement !== sessions) sessions.insertBefore(keeper, sessions.firstChild);
    return keeper;
  }

  function mergeProvidersIntoSessions() {
    const tabs = document.querySelector('.aliver-sidebar .tabs, nav.tabs');
    tabs?.querySelector('button[data-tab="providers"]')?.remove();

    const sessions = document.getElementById('tab-sessions');
    if (!sessions) return false;
    sessions.classList.add('avatar-session-workspace');
    const heading = normalizeSessionHeading(sessions);

    const embedded = document.getElementById('avatar-provider-subpanel');
    if (embedded) {
      if (heading !== sessions.firstElementChild) sessions.prepend(heading);
      return true;
    }

    const providers = document.getElementById('tab-providers');
    if (!providers) return false;
    const originalSessionChildren = [...sessions.children].filter(child => child !== heading);
    const main = document.createElement('section');
    main.id = 'avatar-session-main';
    main.className = 'avatar-session-subpanel';
    originalSessionChildren.forEach(child => main.appendChild(child));

    const switcher = document.createElement('nav');
    switcher.className = 'avatar-session-switcher';
    switcher.setAttribute('aria-label', '数字人会话工作区');
    switcher.setAttribute('role', 'tablist');
    switcher.innerHTML = `
      <button type="button" role="tab" data-session-view="sessions" class="active" aria-selected="true">会话运行与自动恢复</button>
      <button type="button" role="tab" data-session-view="providers" aria-selected="false">供应商与模型配置</button>`;
    switcher.addEventListener('click', event => {
      const button = event.target.closest('button[data-session-view]');
      if (button) activateSessionSubpanel(button.dataset.sessionView);
    });

    providers.id = 'avatar-provider-subpanel';
    providers.classList.remove('tab-panel', 'active');
    providers.classList.add('avatar-session-subpanel', 'embedded-provider-panel');
    providers.hidden = true;

    sessions.replaceChildren(heading, switcher, main, providers);
    const remembered = localStorage.getItem('aliverAvatarWorkspace');
    activateSessionSubpanel(remembered === 'providers' ? 'providers' : 'sessions');
    return true;
  }

  function refineDirector() {
    const director = document.getElementById('tab-director');
    if (!director) return false;
    director.classList.add('director-layout-refined');

    const auto = document.getElementById('tab-auto-director');
    if (auto && auto.parentElement !== director) {
      auto.classList.remove('tab-panel');
      auto.classList.add('director-subspace', 'active');
      director.appendChild(auto);
    }

    const wizard = document.getElementById('director-plan-wizard');
    const autoGrid = director.querySelector('.auto-director-grid');
    if (wizard && autoGrid) {
      const target = autoGrid.parentElement;
      if (target && (wizard.parentElement !== target || wizard.nextElementSibling !== autoGrid)) {
        target.insertBefore(wizard, autoGrid);
      }
      wizard.classList.add('panel', 'director-plan-wide');
    }

    director.querySelectorAll('.director-grid > .panel, .auto-director-grid > .panel').forEach(panel => {
      panel.style.minWidth = '0';
      panel.style.maxWidth = '100%';
    });
    return true;
  }

  function addSessionRestoreNote() {
    const sessions = document.getElementById('tab-sessions');
    if (!sessions) return false;
    const heading = normalizeSessionHeading(sessions);
    let note = document.getElementById('avatar-auto-restore-note');
    if (!note) {
      note = document.createElement('div');
      note.id = 'avatar-auto-restore-note';
      note.className = 'diagnosis good';
      note.textContent = 'Bridge 重新上线后，ALiver 会自动恢复上次因 Bridge 中断的数字人会话；手动停止的会话不会自动重启。';
    }
    if (note.parentElement !== sessions || heading.nextElementSibling !== note) heading.after(note);
    return true;
  }

  function run() {
    injectStyle();
    document.body.classList.add('aliver-ui-stable-layout');
    const merged = mergeProvidersIntoSessions();
    if (merged) addSessionRestoreNote();
    refineDirector();
    return merged;
  }

  function start() {
    STABILIZE_DELAYS.forEach(delay => window.setTimeout(run, delay));
    window.addEventListener('aliver:tabchange', event => {
      const name = event.detail?.name;
      if (name === 'sessions' || name === 'director') window.requestAnimationFrame(run);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
