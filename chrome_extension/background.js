const DEFAULT_SERVER = 'http://127.0.0.1:8765';
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const CHATGPT_URLS = [
  'https://chatgpt.com/*',
  'https://www.chatgpt.com/*',
  'https://chat.openai.com/*',
];
let socket = null;
let reconnectTimer = null;
let keepAliveTimer = null;
let socketState = 'disconnected';

function sleep(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds));
}

function websocketUrl(serverUrl, extensionId, token) {
  const url = new URL(serverUrl || DEFAULT_SERVER);
  const protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${url.host}/ws/extensions/${encodeURIComponent(extensionId)}?token=${encodeURIComponent(token)}`;
}

function isChatGptUrl(url = '') {
  try {
    const parsed = new URL(url);
    return ['chatgpt.com', 'www.chatgpt.com', 'chat.openai.com'].includes(parsed.hostname);
  } catch (_) {
    return false;
  }
}

function conversationKey(url = '') {
  if (!isChatGptUrl(url)) return '';
  try {
    const parsed = new URL(url);
    const pathname = parsed.pathname.replace(/\/+$/, '') || '/';
    return `${parsed.origin}${pathname}`;
  } catch (_) {
    return '';
  }
}

function isMissingReceiverError(error) {
  const message = String(error?.message || error || '');
  return /Receiving end does not exist|Could not establish connection/i.test(message);
}

async function getConfig() {
  return chrome.storage.local.get({
    serverUrl: DEFAULT_SERVER,
    extensionName: 'ALiver ChatGPT Controller',
    extensionId: '',
    extensionToken: '',
    commandResults: {},
    boundTabId: null,
    boundConversationKey: '',
    boundUrl: '',
    boundTitle: '',
    boundAt: null,
  });
}

async function setSocketState(value, error = '') {
  socketState = value;
  await chrome.storage.local.set({
    socketState: value,
    socketError: error,
    socketUpdatedAt: new Date().toISOString(),
  });
}

async function pairExtension({ serverUrl, adminToken, extensionName }) {
  const cleanServer = (serverUrl || DEFAULT_SERVER).replace(/\/$/, '');
  const response = await fetch(`${cleanServer}/api/director/extensions/register`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-ALiver-Token': adminToken || '',
    },
    body: JSON.stringify({
      name: extensionName || 'ALiver ChatGPT Controller',
      browser_name: 'Chrome',
      version: EXTENSION_VERSION,
      metadata: { extension_runtime_id: chrome.runtime.id },
    }),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) {}
    throw new Error(detail);
  }
  const result = await response.json();
  await chrome.storage.local.set({
    serverUrl: cleanServer,
    extensionName: extensionName || 'ALiver ChatGPT Controller',
    extensionId: result.extension_id,
    extensionToken: result.token,
    pairedAt: new Date().toISOString(),
  });
  connectSocket();
  return result;
}

async function listChatGptTabs() {
  const tabs = await chrome.tabs.query({ url: CHATGPT_URLS });
  return tabs
    .filter(tab => Number.isInteger(tab.id) && isChatGptUrl(tab.url || ''))
    .sort((left, right) => {
      const activeDifference = Number(Boolean(right.active)) - Number(Boolean(left.active));
      if (activeDifference) return activeDifference;
      return Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0);
    });
}

async function findChatGptTab() {
  const tabs = await listChatGptTabs();
  if (!tabs.length) throw new Error('No ChatGPT tab is open. Open chatgpt.com first.');
  return tabs[0];
}

async function getTab(tabId) {
  if (!Number.isInteger(tabId)) return null;
  try {
    return await chrome.tabs.get(tabId);
  } catch (_) {
    return null;
  }
}

async function probeContentScript(tabId) {
  const response = await chrome.tabs.sendMessage(tabId, { type: 'aliver.content.ping' });
  return Boolean(response?.ok);
}

async function injectContentScript(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['content.js'],
  });
  await sleep(100);
}

async function ensureContentScript(tab) {
  if (!Number.isInteger(tab?.id)) throw new Error('ChatGPT tab has no valid tab ID.');
  try {
    if (await probeContentScript(tab.id)) return;
  } catch (error) {
    if (!isMissingReceiverError(error)) throw error;
  }
  await injectContentScript(tab.id);
  try {
    if (await probeContentScript(tab.id)) return;
  } catch (error) {
    throw new Error(`ChatGPT page controller injection failed: ${String(error.message || error)}`);
  }
  throw new Error('ChatGPT page controller was injected but did not respond. Reload the ChatGPT tab.');
}

async function sendToChatGpt(tab, message) {
  await ensureContentScript(tab);
  try {
    return await chrome.tabs.sendMessage(tab.id, message);
  } catch (error) {
    if (!isMissingReceiverError(error)) throw error;
    await injectContentScript(tab.id);
    return chrome.tabs.sendMessage(tab.id, message);
  }
}

async function injectIntoOpenTabs() {
  const tabs = await listChatGptTabs();
  await Promise.allSettled(tabs.map(tab => ensureContentScript(tab)));
}

async function bindingStatus() {
  const config = await getConfig();
  if (!Number.isInteger(config.boundTabId)) {
    return {
      bound: false,
      valid: false,
      tabId: null,
      conversationKey: '',
      url: '',
      title: '',
      boundAt: null,
      reason: '尚未绑定目标 ChatGPT 会话',
    };
  }

  const tab = await getTab(config.boundTabId);
  if (!tab || !isChatGptUrl(tab.url || '')) {
    return {
      bound: true,
      valid: false,
      tabId: config.boundTabId,
      conversationKey: config.boundConversationKey || '',
      url: config.boundUrl || '',
      title: config.boundTitle || '',
      boundAt: config.boundAt || null,
      reason: '已绑定的 ChatGPT 标签页已关闭或不可用，请重新绑定。',
    };
  }

  const currentKey = conversationKey(tab.url || '');
  const expectedKey = String(config.boundConversationKey || '');
  const valid = Boolean(currentKey && expectedKey && currentKey === expectedKey);
  return {
    bound: true,
    valid,
    tabId: tab.id,
    windowId: tab.windowId,
    conversationKey: expectedKey,
    currentConversationKey: currentKey,
    url: tab.url || config.boundUrl || '',
    title: tab.title || config.boundTitle || '',
    boundAt: config.boundAt || null,
    reason: valid ? '' : '已绑定标签页已经切换到另一个 ChatGPT 会话，请重新绑定当前会话。',
  };
}

async function bindChatGptTab(tabId) {
  const tab = await getTab(Number(tabId));
  if (!tab || !Number.isInteger(tab.id)) {
    throw new Error('当前标签页不存在，无法绑定。');
  }
  if (!isChatGptUrl(tab.url || '')) {
    throw new Error('当前标签页不是 ChatGPT。请先切换到需要导演控制的 ChatGPT 语音对话页面。');
  }
  const page = await sendToChatGpt(tab, { type: 'aliver.page.status' });
  if (!page?.ok) {
    throw new Error(page?.error || '无法读取当前 ChatGPT 页面状态。');
  }
  const pageUrl = page.data?.url || tab.url || '';
  const key = conversationKey(pageUrl);
  if (!key) throw new Error('无法识别当前 ChatGPT 会话地址。');
  const boundAt = new Date().toISOString();
  await chrome.storage.local.set({
    boundTabId: tab.id,
    boundConversationKey: key,
    boundUrl: pageUrl,
    boundTitle: page.data?.title || tab.title || '',
    boundAt,
  });
  await sendPageStatus();
  return {
    bound: true,
    valid: true,
    tabId: tab.id,
    windowId: tab.windowId,
    conversationKey: key,
    url: pageUrl,
    title: page.data?.title || tab.title || '',
    liveActive: Boolean(page.data?.live_active),
    boundAt,
  };
}

async function clearBinding() {
  await chrome.storage.local.set({
    boundTabId: null,
    boundConversationKey: '',
    boundUrl: '',
    boundTitle: '',
    boundAt: null,
  });
  await sendPageStatus();
  return bindingStatus();
}

async function resolveCommandTarget() {
  const config = await getConfig();
  if (Number.isInteger(config.boundTabId)) {
    const status = await bindingStatus();
    if (!status.valid) throw new Error(status.reason || '已绑定的 ChatGPT 会话当前不可用。');
    const tab = await getTab(config.boundTabId);
    if (!tab) throw new Error('已绑定的 ChatGPT 标签页已关闭，请重新绑定。');
    return tab;
  }

  const tabs = await listChatGptTabs();
  if (!tabs.length) throw new Error('没有打开 ChatGPT 页面。');
  if (tabs.length > 1) {
    throw new Error('检测到多个 ChatGPT 页面。为避免导演指令发错窗口，请在目标语音对话页面点击扩展并选择“绑定当前 ChatGPT 会话”。');
  }
  return tabs[0];
}

async function cachedResult(commandId) {
  const { commandResults = {} } = await getConfig();
  return commandResults[commandId] || null;
}

async function rememberResult(commandId, result) {
  const { commandResults = {} } = await getConfig();
  commandResults[commandId] = result;
  const entries = Object.entries(commandResults).slice(-100);
  await chrome.storage.local.set({ commandResults: Object.fromEntries(entries) });
}

async function executeCommand(message) {
  const prior = await cachedResult(message.command_id);
  if (prior) return prior;
  const tab = await resolveCommandTarget();
  const response = await sendToChatGpt(tab, {
    type: 'aliver.director.command',
    commandId: message.command_id,
    commandType: message.command_type,
    payload: message.payload || {},
  });
  if (!response) throw new Error('ChatGPT page controller did not return a result.');
  const result = {
    ok: Boolean(response.ok),
    data: {
      ...(response.data || {}),
      target_tab_id: tab.id,
      target_window_id: tab.windowId,
      target_conversation_key: conversationKey(tab.url || ''),
    },
    error: response.error || null,
  };
  await rememberResult(message.command_id, result);
  return result;
}

async function statusTarget() {
  const config = await getConfig();
  if (Number.isInteger(config.boundTabId)) {
    const status = await bindingStatus();
    if (!status.valid) return { tab: null, binding: status, tabs: await listChatGptTabs() };
    return { tab: await getTab(config.boundTabId), binding: status, tabs: await listChatGptTabs() };
  }
  const tabs = await listChatGptTabs();
  if (tabs.length === 1) return { tab: tabs[0], binding: await bindingStatus(), tabs };
  return { tab: null, binding: await bindingStatus(), tabs };
}

async function sendPageStatus() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  try {
    const target = await statusTarget();
    if (!target.tab) {
      socket.send(JSON.stringify({
        type: 'page.status',
        url: '',
        metadata: {
          chatgpt_open: target.tabs.length > 0,
          chatgpt_tab_count: target.tabs.length,
          binding_required: target.tabs.length > 1 && !target.binding.bound,
          binding: target.binding,
        },
      }));
      return;
    }
    const page = await sendToChatGpt(target.tab, { type: 'aliver.page.status' });
    socket.send(JSON.stringify({
      type: 'page.status',
      url: target.tab.url || '',
      metadata: {
        tab_title: target.tab.title || '',
        chatgpt_tab_id: target.tab.id,
        chatgpt_window_id: target.tab.windowId,
        chatgpt_tab_count: target.tabs.length,
        binding: target.binding,
        ...(page?.data || {}),
      },
    }));
  } catch (error) {
    socket.send(JSON.stringify({
      type: 'heartbeat',
      metadata: {
        chatgpt_open: false,
        status_error: String(error.message || error),
      },
    }));
  }
}

async function shouldReportTab(tabId) {
  const config = await getConfig();
  if (Number.isInteger(config.boundTabId)) return Number(tabId) === config.boundTabId;
  const tabs = await listChatGptTabs();
  return tabs.length === 1 && Number(tabId) === tabs[0]?.id;
}

async function handleSocketMessage(event) {
  const message = JSON.parse(event.data);
  if (message.type !== 'director.command') return;
  let result;
  try {
    result = await executeCommand(message);
  } catch (error) {
    result = { ok: false, data: {}, error: String(error.message || error) };
  }
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: 'command.result',
      command_id: message.command_id,
      ok: result.ok,
      data: result.data || {},
      error: result.error || null,
    }));
  }
}

async function connectSocket() {
  clearTimeout(reconnectTimer);
  clearInterval(keepAliveTimer);
  const config = await getConfig();
  if (!config.extensionId || !config.extensionToken) {
    await setSocketState('unpaired');
    return;
  }
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  await setSocketState('connecting');
  socket = new WebSocket(websocketUrl(config.serverUrl, config.extensionId, config.extensionToken));
  socket.onopen = async () => {
    await setSocketState('connected');
    socket.send(JSON.stringify({
      type: 'extension.hello',
      metadata: {
        extension_version: EXTENSION_VERSION,
        runtime_id: chrome.runtime.id,
      },
    }));
    await injectIntoOpenTabs();
    await sendPageStatus();
    keepAliveTimer = setInterval(() => sendPageStatus(), 20000);
  };
  socket.onmessage = event => handleSocketMessage(event).catch(console.error);
  socket.onerror = () => setSocketState('error', 'WebSocket connection error');
  socket.onclose = async event => {
    clearInterval(keepAliveTimer);
    await setSocketState('disconnected', `closed ${event.code}`);
    reconnectTimer = setTimeout(connectSocket, 3000);
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'aliver.pair') {
    pairExtension(message.payload)
      .then(result => sendResponse({ ok: true, data: result }))
      .catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }
  if (message.type === 'aliver.status') {
    Promise.all([getConfig(), bindingStatus(), listChatGptTabs()])
      .then(([config, binding, tabs]) => sendResponse({
        ok: true,
        data: {
          serverUrl: config.serverUrl,
          extensionName: config.extensionName,
          extensionId: config.extensionId,
          pairedAt: config.pairedAt || null,
          socketState,
          socketError: config.socketError || '',
          binding,
          chatgptTabCount: tabs.length,
        },
      }))
      .catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }
  if (message.type === 'aliver.bind.tab') {
    bindChatGptTab(Number(message.payload?.tabId))
      .then(result => sendResponse({ ok: true, data: result }))
      .catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }
  if (message.type === 'aliver.unbind') {
    clearBinding()
      .then(result => sendResponse({ ok: true, data: result }))
      .catch(error => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }
  if (message.type === 'aliver.reconnect') {
    if (socket) socket.close(1000, 'manual reconnect');
    socket = null;
    connectSocket().then(() => sendResponse({ ok: true }));
    return true;
  }
  if (message.type === 'aliver.page.status.changed' && socket?.readyState === WebSocket.OPEN) {
    shouldReportTab(sender.tab?.id)
      .then(report => {
        if (!report || !socket || socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({
          type: 'page.status',
          url: sender.tab?.url || '',
          metadata: {
            ...(message.data || {}),
            chatgpt_tab_id: sender.tab?.id ?? null,
            chatgpt_window_id: sender.tab?.windowId ?? null,
          },
        }));
      })
      .catch(console.debug);
  }
  return false;
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete' || !isChatGptUrl(tab.url || '')) return;
  ensureContentScript({ ...tab, id: tabId })
    .then(() => sendPageStatus())
    .catch(console.debug);
});

chrome.tabs.onRemoved.addListener(() => {
  sendPageStatus().catch(console.debug);
});

async function initializeExtension() {
  await injectIntoOpenTabs();
  await connectSocket();
}

chrome.runtime.onInstalled.addListener(() => initializeExtension().catch(console.error));
chrome.runtime.onStartup.addListener(() => initializeExtension().catch(console.error));
initializeExtension().catch(console.error);
