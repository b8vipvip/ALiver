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

  function setRouteAvailability(data) {
    const ready = Boolean(data.recommendation?.ready);
    const start = document.getElementById('dsp-start');
    const apply = document.getElementById('dsp-apply');
    if (start) start.disabled = !ready;
    if (apply) apply.disabled = !ready;

    if (ready) return;
    const box = document.getElementById('dsp-main-result');
    if (!box) return;
    box.className = 'diagnosis bad';
    box.textContent = (data.recommendation?.warnings || []).join('；')
      || '没有独立的 DSP 输出虚拟声卡。请安装 CABLE-B，不能复用 GPT_IN 的 CABLE-A。';
  }

  async function refreshSelectors() {
    if (busy || typeof sendBridgeCommand !== 'function') return;
    const bridgeId = document.getElementById('dsp-bridge')?.value || '';
    const input = document.getElementById('dsp-input');
    const output = document.getElementById('dsp-output');
    if (!bridgeId || !input || !output) return;
    busy = true;
    try {
      const data = await sendBridgeCommand(bridgeId, 'audio.dsp.devices', {}, 30);
      const inputCurrent = data.config?.input_device_key
        || data.recommendation?.input_microphone?.key || '';
      const outputCurrent = data.config?.output_device_key
        || data.recommendation?.output_playback?.key || '';
      const forbidden = new Set(data.recommendation?.forbidden_output_families || []);

      input.innerHTML = '<option value="">请选择原声录音端</option>'
        + (data.input_devices || [])
          .filter(row => row.is_virtual)
          .map(row => `<option value="${escape(row.key)}">${escape(deviceLabel(row))}</option>`)
          .join('');
      if ([...input.options].some(option => option.value === inputCurrent)) input.value = inputCurrent;

      const outputs = (data.output_devices || [])
        .filter(row => row.is_virtual && !forbidden.has(row.virtual_family));
      output.innerHTML = '<option value="">请选择独立的处理后输出</option>'
        + outputs
          .map(row => `<option value="${escape(row.key)}">${escape(deviceLabel(row))}</option>`)
          .join('');
      if ([...output.options].some(option => option.value === outputCurrent)) {
        output.value = outputCurrent;
      } else if (data.recommendation?.output_playback?.key) {
        output.value = data.recommendation.output_playback.key;
      } else {
        output.value = '';
      }

      correctLabel();
      setRouteAvailability(data);
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
    if (input.dataset.recordingEndpointPatch === '2') return;
    input.dataset.recordingEndpointPatch = '2';
    correctLabel();
    bridge.addEventListener('change', () => window.setTimeout(refreshSelectors, 150));
    document.getElementById('dsp-refresh')?.addEventListener(
      'click',
      () => window.setTimeout(refreshSelectors, 500),
    );
    window.addEventListener('aliver:tabchange', event => {
      if (event.detail?.name === 'voice-lab') window.setTimeout(refreshSelectors, 150);
    });
    window.setTimeout(refreshSelectors, 500);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
