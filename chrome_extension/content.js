(() => {
  const CONTENT_VERSION = '0.1.3';
  const STATE_KEY = '__ALIVER_CHATGPT_CONTENT_CONTROLLER__';
  const previous = globalThis[STATE_KEY];

  function safeRuntimeId() {
    try {
      return chrome?.runtime?.id || '';
    } catch (_) {
      return '';
    }
  }

  function safeRemoveListener(listener) {
    if (!listener) return;
    try {
      if (safeRuntimeId()) chrome.runtime.onMessage.removeListener(listener);
    } catch (_) {}
  }

  if (previous?.version === CONTENT_VERSION && !previous?.stopped) return;
  if (previous?.intervalId) clearInterval(previous.intervalId);
  if (previous?.startupTimerId) clearTimeout(previous.startupTimerId);
  safeRemoveListener(previous?.listener);

  const state = {
    version: CONTENT_VERSION,
    listener: null,
    intervalId: null,
    startupTimerId: null,
    stopped: false,
    stopReason: '',
  };
  globalThis[STATE_KEY] = state;

  function stopController(reason = 'extension context invalidated') {
    if (state.stopped) return;
    state.stopped = true;
    state.stopReason = reason;
    if (state.intervalId) {
      clearInterval(state.intervalId);
      state.intervalId = null;
    }
    if (state.startupTimerId) {
      clearTimeout(state.startupTimerId);
      state.startupTimerId = null;
    }
    safeRemoveListener(state.listener);
  }

  function extensionContextAvailable() {
    const available = Boolean(safeRuntimeId());
    if (!available) stopController();
    return available;
  }

  function isContextInvalidatedError(error) {
    return /Extension context invalidated|context invalidated/i.test(String(error?.message || error || ''));
  }

  async function safeRuntimeSendMessage(message) {
    if (!extensionContextAvailable()) return null;
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (error) {
      if (isContextInvalidatedError(error) || !safeRuntimeId()) {
        stopController(String(error?.message || error || 'extension context invalidated'));
        return null;
      }
      throw error;
    }
  }

  const COMMAND_SELECTOR = '#prompt-textarea, textarea[placeholder], div[contenteditable="true"]';

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }

  function findComposer() {
    const preferred = document.querySelector('#prompt-textarea');
    if (visible(preferred)) return preferred;
    return [...document.querySelectorAll(COMMAND_SELECTOR)].find(visible) || null;
  }

  function isGenerating() {
    return Boolean(
      document.querySelector('button[data-testid="stop-button"]') ||
      [...document.querySelectorAll('button')].find(button =>
        /stop generating|停止生成/i.test(button.getAttribute('aria-label') || button.textContent || '')
      )
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

  async function waitFor(predicate, timeout = 3000, interval = 100) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const value = predicate();
      if (value) return value;
      await new Promise(resolve => setTimeout(resolve, interval));
    }
    return null;
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

  function detectLiveActive() {
    const text = document.body.innerText || '';
    return Boolean(
      document.querySelector('[data-testid*="voice"]') ||
      document.querySelector('button[aria-label*="结束语音"]') ||
      /结束语音|end voice|voice mode/i.test(text)
    );
  }

  async function sendText(payload) {
    const text = String(payload.text || '').trim();
    if (!text) throw new Error('Director command text is empty.');
    if (isGenerating() && !payload.force) {
      throw new Error('ChatGPT is currently responding. Wait or enable force send.');
    }
    const composer = await waitFor(findComposer, 5000);
    if (!composer) throw new Error('ChatGPT composer was not found. Reload the page and try again.');
    setComposerText(composer, text);
    await new Promise(resolve => setTimeout(resolve, 250));
    let sent = false;
    if (payload.auto_send !== false) {
      const button = await waitFor(findSendButton, 2500);
      if (button) {
        button.click();
        sent = true;
      } else {
        composer.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'Enter',
          code: 'Enter',
          keyCode: 13,
          which: 13,
          bubbles: true,
        }));
        sent = true;
      }
    }
    return {
      sent,
      inserted: true,
      text_length: text.length,
      url: location.href,
      title: document.title,
      live_active: detectLiveActive(),
      content_script_version: CONTENT_VERSION,
    };
  }

  function pageStatus() {
    return {
      chatgpt_open: true,
      composer_ready: Boolean(findComposer()),
      generating: isGenerating(),
      live_active: detectLiveActive(),
      url: location.href,
      title: document.title,
      content_script_version: CONTENT_VERSION,
    };
  }

  const listener = (message, sender, sendResponse) => {
    if (message.type === 'aliver.content.ping') {
      sendResponse({ ok: true, data: pageStatus() });
      return false;
    }
    if (message.type === 'aliver.director.command') {
      if (!['send_text', 'director_instruction'].includes(message.commandType)) {
        sendResponse({ ok: false, error: `Unsupported command type: ${message.commandType}` });
        return false;
      }
      sendText(message.payload || {})
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
      return true;
    }
    if (message.type === 'aliver.page.status') {
      sendResponse({ ok: true, data: pageStatus() });
      return false;
    }
    return false;
  };
  state.listener = listener;

  if (!extensionContextAvailable()) return;
  try {
    chrome.runtime.onMessage.addListener(listener);
  } catch (error) {
    stopController(String(error?.message || error));
    return;
  }

  let lastStatus = '';
  const reportStatus = async () => {
    if (state.stopped || !extensionContextAvailable()) return;
    const data = pageStatus();
    const serialized = JSON.stringify(data);
    if (serialized === lastStatus) return;
    lastStatus = serialized;
    try {
      await safeRuntimeSendMessage({ type: 'aliver.page.status.changed', data });
    } catch (error) {
      // A live extension context may still reject a transient message if the
      // service worker is restarting. That is harmless; the next interval retries.
      if (isContextInvalidatedError(error)) stopController(String(error.message || error));
    }
  };

  state.intervalId = setInterval(() => {
    reportStatus().catch(error => {
      if (isContextInvalidatedError(error)) stopController(String(error.message || error));
    });
  }, 5000);
  state.startupTimerId = setTimeout(() => {
    reportStatus().catch(error => {
      if (isContextInvalidatedError(error)) stopController(String(error.message || error));
    });
  }, 250);
})();
