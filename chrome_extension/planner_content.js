(() => {
  const VERSION = '0.1.5';
  const STATE_KEY = '__ALIVER_CHATGPT_PLANNER_CONTENT__';
  const previous = globalThis[STATE_KEY];
  if (previous?.listener) {
    try { chrome.runtime.onMessage.removeListener(previous.listener); } catch (_) {}
  }

  const state = { version: VERSION, listener: null, busy: false };
  globalThis[STATE_KEY] = state;

  const COMPOSER_SELECTOR = '#prompt-textarea, textarea[placeholder], div[contenteditable="true"]';

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }

  function findComposer() {
    const preferred = document.querySelector('#prompt-textarea');
    if (visible(preferred)) return preferred;
    return [...document.querySelectorAll(COMPOSER_SELECTOR)].find(visible) || null;
  }

  function findSendButton() {
    const selectors = [
      'button[data-testid="send-button"]',
      'button[aria-label="Send prompt"]',
      'button[aria-label*="发送"]',
      'form button[type="submit"]',
    ];
    for (const selector of selectors) {
      const button = [...document.querySelectorAll(selector)].find(visible);
      if (button && !button.disabled) return button;
    }
    return null;
  }

  function isGenerating() {
    return Boolean(
      document.querySelector('button[data-testid="stop-button"]') ||
      [...document.querySelectorAll('button')].find(button =>
        /stop generating|停止生成/i.test(button.getAttribute('aria-label') || button.textContent || '')
      )
    );
  }

  function detectLiveActive() {
    const text = document.body.innerText || '';
    return Boolean(
      document.querySelector('[data-testid*="voice"]') ||
      document.querySelector('button[aria-label*="结束语音"]') ||
      /结束语音|end voice|voice mode/i.test(text)
    );
  }

  function nativeSetValue(element, value) {
    const prototype = element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
    descriptor?.set?.call(element, value);
  }

  function setComposerText(element, text) {
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      nativeSetValue(element, text);
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('insertText', false, text);
    if (!element.textContent?.trim()) {
      element.replaceChildren(Object.assign(document.createElement('p'), { textContent: text }));
    }
    element.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertText',
      data: text,
    }));
  }

  async function sleep(milliseconds) {
    await new Promise(resolve => setTimeout(resolve, milliseconds));
  }

  async function waitFor(predicate, timeout = 5000, interval = 100) {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const value = predicate();
      if (value) return value;
      await sleep(interval);
    }
    return null;
  }

  function assistantNodes() {
    const selectors = [
      '[data-message-author-role="assistant"]',
      'article[data-testid^="conversation-turn"] [data-message-author-role="assistant"]',
    ];
    const result = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const node of document.querySelectorAll(selector)) {
        if (seen.has(node) || !visible(node)) continue;
        seen.add(node);
        result.push(node);
      }
    }
    return result;
  }

  function responseText(node) {
    const preferred = node?.querySelector('.markdown, [class*="markdown"], [data-message-content]');
    return (preferred?.innerText || node?.innerText || node?.textContent || '')
      .trim()
      .replace(/\n{3,}/g, '\n\n')
      .slice(0, 30000);
  }

  function hash(value) {
    let result = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return (result >>> 0).toString(16).padStart(8, '0');
  }

  function assistantSnapshot() {
    const nodes = assistantNodes();
    const node = nodes[nodes.length - 1] || null;
    if (!node) return { node: null, key: '', text: '' };
    const text = responseText(node);
    const turn = node.closest('[data-message-id], article[id], article[data-testid]');
    const explicit =
      node.getAttribute('data-message-id') ||
      turn?.getAttribute('data-message-id') ||
      turn?.id ||
      turn?.getAttribute('data-testid') ||
      '';
    const key = explicit ? `${location.pathname}|${explicit}|${hash(text)}` : `${location.pathname}|${hash(text)}`;
    return { node, key, text };
  }

  async function sendPrompt(text) {
    const composer = await waitFor(findComposer, 7000);
    if (!composer) throw new Error('没有找到 ChatGPT 文字输入框，请刷新当前绑定页面。');
    setComposerText(composer, text);
    await sleep(250);
    const button = await waitFor(findSendButton, 3000);
    if (button) {
      button.click();
      return;
    }
    composer.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
    }));
  }

  async function generatePlan(payload) {
    if (state.busy) throw new Error('当前 ChatGPT 窗口已有一个策划任务正在执行。');
    if (payload.require_voice_inactive !== false && detectLiveActive()) {
      throw new Error('当前 ChatGPT 正在语音对话。请先结束语音模式，再生成直播方案。');
    }
    if (isGenerating()) throw new Error('当前 ChatGPT 正在回答，请等待结束后再生成方案。');
    const text = String(payload.text || '').trim();
    if (!text) throw new Error('直播方案策划提示词为空。');

    state.busy = true;
    const started = Date.now();
    const timeoutMs = Math.max(30000, Math.min(Number(payload.timeout_ms || 170000), 240000));
    const before = assistantSnapshot();
    let candidateKey = '';
    let candidateText = '';
    let stableSince = 0;
    globalThis.__ALIVER_PLANNER_ACTIVE__ = {
      active: true,
      requestId: String(payload.request_id || ''),
      startedAt: started,
    };

    try {
      await sendPrompt(text);
      while (Date.now() - started < timeoutMs) {
        const current = assistantSnapshot();
        const isNew = Boolean(current.node && current.text && current.key !== before.key);
        if (isNew) {
          const changed = current.key !== candidateKey || current.text !== candidateText;
          if (changed) {
            candidateKey = current.key;
            candidateText = current.text;
            stableSince = Date.now();
          }
          if (!isGenerating() && Date.now() - stableSince >= 1200) {
            current.node.dataset.aliverPlannerResponse = String(payload.request_id || '1');
            return {
              response_text: current.text,
              message_key: current.key,
              request_id: String(payload.request_id || ''),
              elapsed_ms: Date.now() - started,
              url: location.href,
              title: document.title,
              live_active: detectLiveActive(),
              content_script_version: VERSION,
            };
          }
        }
        await sleep(250);
      }
      throw new Error(`等待 ChatGPT 策划回答超过 ${Math.ceil(timeoutMs / 1000)} 秒。`);
    } finally {
      state.busy = false;
      globalThis.__ALIVER_PLANNER_ACTIVE__ = { active: false };
    }
  }

  const listener = (message, sender, sendResponse) => {
    if (message.type !== 'aliver.plan.generate') return false;
    generatePlan(message.payload || {})
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  };
  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
