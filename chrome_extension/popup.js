const $ = id => document.getElementById(id);

async function runtimeMessage(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error || 'Unknown extension error');
  return response.data || {};
}

function renderStatus(data) {
  $('server-url').value = data.serverUrl || 'http://127.0.0.1:8765';
  $('extension-name').value = data.extensionName || 'ALiver ChatGPT Controller';
  $('status').innerHTML = `
    <strong>连接：${data.socketState || 'unknown'}</strong>
    <div>扩展 ID：${data.extensionId || '尚未配对'}</div>
    <div>服务端：${data.serverUrl || ''}</div>
    ${data.socketError ? `<div class="error">${data.socketError}</div>` : ''}
  `;
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
    $('status').innerHTML = `<div class="error">${error.message}</div>`;
  } finally {
    $('pair').disabled = false;
  }
});

$('reconnect').addEventListener('click', async () => {
  await runtimeMessage({ type: 'aliver.reconnect' });
  setTimeout(refresh, 500);
});

$('open-dashboard').addEventListener('click', () => {
  chrome.tabs.create({ url: $('server-url').value.trim() || 'http://127.0.0.1:8765' });
});

refresh();
setInterval(refresh, 2000);
