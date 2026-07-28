const $ = id => document.getElementById(id);

async function runtimeMessage(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error || 'Unknown extension error');
  return response.data || {};
}

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function renderBinding(binding = {}, tabCount = 0) {
  const valid = Boolean(binding.bound && binding.valid);
  const state = valid ? '已绑定' : binding.bound ? '绑定失效' : '未绑定';
  const detail = valid
    ? `Tab ${binding.tabId} · Window ${binding.windowId ?? '-'}<br>${escapeHtml(binding.url || binding.conversationKey || '')}`
    : escapeHtml(binding.reason || '请在目标 ChatGPT 语音对话页面绑定当前会话。');
  $('binding-status').innerHTML = `
    <div class="binding-state ${valid ? 'ok' : 'warn'}">${state}</div>
    <div>${detail}</div>
    <div>当前检测到 ChatGPT 标签页：${tabCount}</div>
  `;
  $('unbind').disabled = !binding.bound;
}

function renderStatus(data) {
  $('server-url').value = data.serverUrl || 'http://127.0.0.1:8765';
  $('extension-name').value = data.extensionName || 'ALiver ChatGPT Controller';
  $('status').innerHTML = `
    <strong>连接：${escapeHtml(data.socketState || 'unknown')}</strong>
    <div>扩展 ID：${escapeHtml(data.extensionId || '尚未配对')}</div>
    <div>服务端：${escapeHtml(data.serverUrl || '')}</div>
    ${data.socketError ? `<div class="error">${escapeHtml(data.socketError)}</div>` : ''}
  `;
  renderBinding(data.binding || {}, Number(data.chatgptTabCount || 0));
}

async function refresh() {
  renderStatus(await runtimeMessage({ type: 'aliver.status' }));
}

$('pair').addEventListener('click', async () => {
  $('pair').disabled = true;
  try {
    await runtimeMessage({
      type: 'aliver.pair',
      payload: {
        serverUrl: $('server-url').value.trim(),
        extensionName: $('extension-name').value.trim(),
        adminToken: $('admin-token').value.trim(),
      },
    });
    $('admin-token').value = '';
    await refresh();
  } catch (error) {
    $('status').innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  } finally {
    $('pair').disabled = false;
  }
});

$('reconnect').addEventListener('click', async () => {
  await runtimeMessage({ type: 'aliver.reconnect' });
  setTimeout(refresh, 500);
});

$('bind-current').addEventListener('click', async () => {
  $('bind-current').disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!Number.isInteger(tab?.id)) throw new Error('无法读取当前浏览器标签页。');
    const binding = await runtimeMessage({
      type: 'aliver.bind.tab',
      payload: { tabId: tab.id },
    });
    renderBinding(binding, (await chrome.tabs.query({ url: ['https://chatgpt.com/*', 'https://www.chatgpt.com/*', 'https://chat.openai.com/*'] })).length);
    await refresh();
  } catch (error) {
    $('binding-status').innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  } finally {
    $('bind-current').disabled = false;
  }
});

$('unbind').addEventListener('click', async () => {
  $('unbind').disabled = true;
  try {
    await runtimeMessage({ type: 'aliver.unbind' });
    await refresh();
  } catch (error) {
    $('binding-status').innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  } finally {
    $('unbind').disabled = false;
  }
});

$('open-dashboard').addEventListener('click', () => {
  chrome.tabs.create({ url: $('server-url').value.trim() || 'http://127.0.0.1:8765' });
});

refresh();
setInterval(refresh, 2000);
