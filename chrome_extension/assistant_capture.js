(() => {
  const VERSION = '0.1.4';
  const STATE_KEY = '__ALIVER_ASSISTANT_CAPTURE__';
  const STORAGE_KEY = 'aliverReportedAssistantKeys';
  const previous = globalThis[STATE_KEY];
  if (previous?.stop) previous.stop();

  const state = {
    version: VERSION,
    stopped: false,
    timer: null,
    observer: null,
    location: location.href,
    candidateKey: '',
    candidateSince: 0,
    initialized: false,
    reported: new Set(),
    busy: false,
    lastError: '',
  };
  globalThis[STATE_KEY] = state;

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }

  function isGenerating() {
    return Boolean(
      document.querySelector('button[data-testid="stop-button"]') ||
      [...document.querySelectorAll('button')].find(button =>
        /stop generating|停止生成/i.test(button.getAttribute('aria-label') || button.textContent || '')
      )
    );
  }

  function assistantNodes() {
    const selectors = [
      '[data-message-author-role="assistant"]',
      'article[data-testid^="conversation-turn"] [data-message-author-role="assistant"]',
    ];
    const values = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const node of document.querySelectorAll(selector)) {
        if (seen.has(node) || !visible(node)) continue;
        seen.add(node);
        values.push(node);
      }
    }
    return values;
  }

  function responseText(node) {
    const preferred = node.querySelector('.markdown, [class*="markdown"], [data-message-content]');
    const text = (preferred?.innerText || node.innerText || node.textContent || '').trim();
    return text.replace(/\n{3,}/g, '\n\n').slice(0, 12000);
  }

  function hash(value) {
    let result = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return (result >>> 0).toString(16).padStart(8, '0');
  }

  function messageIdentity(node, text) {
    const turn = node.closest('[data-message-id], article[id], article[data-testid]');
    const explicit =
      node.getAttribute('data-message-id') ||
      turn?.getAttribute('data-message-id') ||
      turn?.id ||
      turn?.getAttribute('data-testid') ||
      '';
    return explicit ? `${location.pathname}|${explicit}|${hash(text)}` : `${location.pathname}|${hash(text)}`;
  }

  async function config() {
    return chrome.storage.local.get({
      serverUrl: 'http://127.0.0.1:8765',
      extensionId: '',
      extensionToken: '',
      [STORAGE_KEY]: [],
    });
  }

  async function remember(key) {
    state.reported.add(key);
    const values = [...state.reported].slice(-150);
    await chrome.storage.local.set({ [STORAGE_KEY]: values });
  }

  async function report(node, text, key) {
    const settings = await config();
    if (!settings.extensionId || !settings.extensionToken) return false;
    const server = String(settings.serverUrl || 'http://127.0.0.1:8765').replace(/\/$/, '');
    const response = await fetch(
      `${server}/api/voice/extensions/${encodeURIComponent(settings.extensionId)}/assistant-completed`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Extension-Token': settings.extensionToken,
        },
        body: JSON.stringify({
          message_id: key,
          text,
          url: location.href,
          title: document.title,
          observed_at: new Date().toISOString(),
        }),
      },
    );
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    await remember(key);
    node.dataset.aliverAssistantReported = '1';
    return true;
  }

  async function scan() {
    if (state.stopped || state.busy) return;
    if (state.location !== location.href) {
      state.location = location.href;
      state.candidateKey = '';
      state.candidateSince = 0;
      state.initialized = false;
    }

    const nodes = assistantNodes();
    const latest = nodes[nodes.length - 1];
    if (!latest) return;
    const text = responseText(latest);
    if (!text) return;
    const key = messageIdentity(latest, text);

    if (!state.initialized) {
      state.initialized = true;
      if (!state.reported.has(key)) await remember(key);
      latest.dataset.aliverAssistantReported = '1';
      return;
    }
    if (state.reported.has(key) || latest.dataset.aliverAssistantReported === '1') return;
    if (isGenerating()) {
      state.candidateKey = '';
      state.candidateSince = 0;
      return;
    }
    if (state.candidateKey !== key) {
      state.candidateKey = key;
      state.candidateSince = Date.now();
      return;
    }
    if (Date.now() - state.candidateSince < 1000) return;

    state.busy = true;
    try {
      await report(latest, text, key);
      state.lastError = '';
    } catch (error) {
      state.lastError = String(error?.message || error);
      console.warn('ALiver assistant capture failed:', state.lastError);
    } finally {
      state.busy = false;
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => scan().catch(console.warn), 350);
  }

  state.stop = () => {
    state.stopped = true;
    clearTimeout(state.timer);
    state.observer?.disconnect();
  };

  config().then(settings => {
    for (const key of settings[STORAGE_KEY] || []) state.reported.add(String(key));
    state.observer = new MutationObserver(schedule);
    state.observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    window.setInterval(() => scan().catch(console.warn), 1500);
    schedule();
  }).catch(console.warn);
})();
