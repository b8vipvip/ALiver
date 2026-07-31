(() => {
  let busy = false;

  function escape(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(value ?? ''));
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function deviceLabel(row) {
    return `${row.name} · ${row.default_sample_rate || '?'}Hz · ${row.virtual_family || row.kind}`;
  }

  function correctLabel() {
    const select = document.getElementById('dsp-input');
    const label = select?.closest('label');
    if (!label) return;
    const text = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE);
    if (text) text.textContent = '原声音频输入（虚拟声卡录音端）';
  }

  async function refreshInputSelector() {
    if (busy || typeof sendBridgeCommand !== 'function') return;
    const bridgeId = document.getElementById('dsp-bridge')?.value || '';
    const select = document.getElementById('dsp-input');
    if (!bridgeId || !select) return;
    busy = true;
    try {
      const data = await sendBridgeCommand(bridgeId, 'audio.dsp.devices', {}, 30);
      const current = data.config?.input_device_key || data.recommendation?.input_microphone?.key || '';
      select.innerHTML = '<option value="">请选择原声录音端</option>'
        + (data.input_devices || [])
          .filter(row => row.is_virtual)
          .map(row => `<option value="${escape(row.key)}">${escape(deviceLabel(row))}</option>`)
          .join('');
      if ([...select.options].some(option => option.value === current)) select.value = current;
      correctLabel();
    } catch (_) {
      // The main DSP panel already renders the command error. Avoid duplicate toasts.
    } finally {
      busy = false;
    }
  }

  function install() {
    const bridge = document.getElementById('dsp-bridge');
    const input = document.getElementById('dsp-input');
    if (!bridge || !input) {
      window.setTimeout(install, 200);
      return;
    }
    if (input.dataset.recordingEndpointPatch === '1') return;
    input.dataset.recordingEndpointPatch = '1';
    correctLabel();
    bridge.addEventListener('change', () => window.setTimeout(refreshInputSelector, 150));
    document.getElementById('dsp-refresh')?.addEventListener(
      'click',
      () => window.setTimeout(refreshInputSelector, 500),
    );
    window.addEventListener('aliver:tabchange', event => {
      if (event.detail?.name === 'voice-lab') window.setTimeout(refreshInputSelector, 150);
    });
    window.setTimeout(refreshInputSelector, 500);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
