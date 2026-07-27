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
  const tab = await findChatGptTab();
  const response = await sendToChatGpt(tab, {
    type: 'aliver.director.command',
    commandId: message.command_id,
    commandType: message.command_type,
    payload: message.payload || {},
  });
  if (!response) throw new Error('ChatGPT page controller did not return a result.');
  const result = {
    ok: Boolean(response.ok),
    data: response.data || {},
    error: response.error || null,
  };
  await rememberResult(message.command_id, result);
  return result;
}

async function sendPageStatus() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  try {
    const tab = await findChatGptTab();
    const page = await sendToChatGpt(tab, { type: 'aliver.page.status' });
    socket.send(JSON.stringify({
      type: 'page.status',
      url: tab.url || '',
      metadata: {
        tab_title: tab.title || '',
        chatgpt_tab_id: tab.id,
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
    getConfig().then(config => sendResponse({
      ok: true,
      data: {
        serverUrl: config.serverUrl,
        extensionName: config.extensionName,
        extensionId: config.extensionId,
        pairedAt: config.pairedAt || null,
        socketState,
        socketError: config.socketError || '',
      },
    }));
    return true;
  }
  if (message.type === 'aliver.reconnect') {
    if (socket) socket.close(1000, 'manual reconnect');
    socket = null;
    connectSocket().then(() => sendResponse({ ok: true }));
    return true;
  }
  if (message.type === 'aliver.page.status.changed' && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: 'page.status',
      url: sender.tab?.url || '',
      metadata: message.data || {},
    }));
  }
  return false;
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete' || !isChatGptUrl(tab.url || '')) return;
  ensureContentScript({ ...tab, id: tabId })
    .then(() => sendPageStatus())
    .catch(console.debug);
});

async function initializeExtension() {
  await injectIntoOpenTabs();
  await connectSocket();
}

chrome.runtime.onInstalled.addListener(() => initializeExtension().catch(console.error));
chrome.runtime.onStartup.addListener(() => initializeExtension().catch(console.error));
initializeExtension().catch(console.error);
