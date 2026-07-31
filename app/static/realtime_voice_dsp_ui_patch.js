(() => {
  let busy = false;
  let installed = false;

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(value ?? ''));
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function pairByFamily(data, family) {
    return (data.virtual_pairs || []).find(row => row.family === family) || null;
  }

  function setSelectAsAutomatic(select, row, prefix) {
    if (!select) return;
    const value = row?.key || '';
    const name = row?.name || '未找到设备';
    select.innerHTML = `<option value="${esc(value)}">自动：${esc(prefix)} · ${esc(name)}</option>`;
    select.value = value;
    select.disabled = true;
    select.dataset.autoRouted = '1';
  }

  function setLabel(select, text) {
    const label = select?.closest('label');
    if (!label) return;
    const node = [...label.childNodes].find(item => item.nodeType === Node.TEXT_NODE);
    if (node) node.textContent = text;
  }

  function resultBox(message, good = true) {
    const box = document.getElementById('dsp-main-result');
    if (!box) return;
    box.className = `diagnosis ${good ? 'good' : 'bad'}`;
    box.textContent = message;
  }

  function showAutomaticEndpoints(input, output) {
    setSelectAsAutomatic(
      document.getElementById('dsp-input'),
      input,
      'CABLE Output 原声录音端',
    );
    setSelectAsAutomatic(
      document.getElementById('dsp-output'),
      output,
      'CABLE-B Input 处理后写入端',
    );
    setLabel(document.getElementById('dsp-input'), '原声音频输入（自动配置）');
    setLabel(document.getElementById('dsp-output'), '处理后输出（自动配置）');
  }

  async function connectedBridge() {
    const rows = await api('/api/bridges');
    const online = rows.filter(row => row.connected === true);
    const select = document.getElementById('dsp-bridge');
    const previous = select?.value || '';
    if (select) {
      select.innerHTML = '<option value="">请选择已连接 Bridge</option>' + online.map(row =>
        `<option value="${esc(row.id)}">${esc(row.name)} · ${esc(row.machine_name || '')} · ${esc(row.version || '')}</option>`
      ).join('');
      if (online.some(row => row.id === previous)) select.value = previous;
      else if (online.length === 1) select.value = online[0].id;
    }
    if (!online.length) throw new Error('没有活动的 Bridge WebSocket 连接。请确认 Bridge 终端仍显示 connected to ALiver。');
    if (online.length > 1 && !select?.value) throw new Error('检测到多个在线 Bridge，请先选择执行节点。');
    return select?.value || online[0].id;
  }

  async function autoConfigure(showMessage = true) {
    if (busy || typeof sendBridgeCommand !== 'function') return null;
    busy = true;
    try {
      const bridgeId = await connectedBridge();
      const liveStatus = await sendBridgeCommand(bridgeId, 'audio.dsp.status', {}, 10);

      // PortAudio device enumeration must not run while the realtime stream is
      // active. Use the already-resolved endpoints from the running status.
      if (liveStatus.running) {
        showAutomaticEndpoints(liveStatus.input_device, liveStatus.output_device);
        const start = document.getElementById('dsp-start');
        const apply = document.getElementById('dsp-apply');
        if (start) start.disabled = false;
        if (apply) apply.disabled = false;
        if (showMessage) {
          resultBox('DSP 正在运行，已沿用锁定的标准 CABLE → CABLE-B 路由；未重新扫描音频设备。');
        }
        return { bridgeId, data: { status: liveStatus } };
      }

      const data = await sendBridgeCommand(bridgeId, 'audio.dsp.devices', {}, 30);
      const raw = pairByFamily(data, 'vb-cable');
      const gptIn = pairByFamily(data, 'vb-cable-a');
      const processed = pairByFamily(data, 'vb-cable-b');

      const missing = [];
      if (!raw?.playback || !raw?.microphone || !raw?.loopback) missing.push('标准 VB-CABLE');
      if (!gptIn?.playback || !gptIn?.microphone) missing.push('CABLE-A');
      if (!processed?.playback || !processed?.microphone) missing.push('CABLE-B');
      if (missing.length) {
        throw new Error(`自动路由缺少完整设备端点：${missing.join('、')}。请在 Windows 声音设置中启用对应播放端和录音端。`);
      }

      await sendBridgeCommand(bridgeId, 'audio.routes.save', {
        gpt_out_capture_key: raw.loopback.key,
        gpt_in_playback_key: gptIn.playback.key,
      }, 30);
      const configured = await sendBridgeCommand(bridgeId, 'audio.dsp.configure', {
        input_device_key: raw.microphone.key,
        output_device_key: processed.playback.key,
      }, 30);

      showAutomaticEndpoints(raw.microphone, processed.playback);
      const start = document.getElementById('dsp-start');
      const apply = document.getElementById('dsp-apply');
      if (start) start.disabled = false;
      if (apply) apply.disabled = false;
      if (showMessage) {
        resultBox('三线音频路由已自动配置：标准 CABLE 接收 ChatGPT 原声，CABLE-A 专用于 GPT_IN，CABLE-B 输出 DSP 处理后声音。');
      }
      return { bridgeId, data: { ...data, status: configured }, raw, gptIn, processed };
    } catch (error) {
      resultBox(error.message, false);
      const start = document.getElementById('dsp-start');
      const apply = document.getElementById('dsp-apply');
      if (start) start.disabled = true;
      if (apply) apply.disabled = true;
      throw error;
    } finally {
      busy = false;
    }
  }

  function renderRecording(result) {
    const box = document.getElementById('dsp-ab-result');
    if (!box) return;
    box.className = 'dsp-ab-result diagnosis good';
    box.innerHTML = `<strong>A/B 录制完成</strong><br>
      原声：${esc(result.original_path)}<br>
      处理后：${esc(result.processed_path)}<br>
      电平：${esc(result.original?.dbfs)} dBFS → ${esc(result.processed?.dbfs)} dBFS<br>
      频谱重心：${esc(result.original?.spectral_centroid_hz)} Hz → ${esc(result.processed?.spectral_centroid_hz)} Hz`;
  }

  async function recordCompare(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    const button = document.getElementById('dsp-record');
    const box = document.getElementById('dsp-ab-result');
    const old = button.textContent;
    button.disabled = true;
    button.textContent = '录制中 10 秒…';
    try {
      const bridgeId = await connectedBridge();
      const status = await sendBridgeCommand(bridgeId, 'audio.dsp.status', {}, 10);
      if (!status.running) throw new Error('请先启动实时 DSP，再录制 A/B 对比。');
      showAutomaticEndpoints(status.input_device, status.output_device);
      box.className = 'dsp-ab-result diagnosis warn';
      box.textContent = '录制已开始，请马上到 ChatGPT 点击“更多操作 → 朗读”。录制期间不会扫描或重启音频设备。';
      const result = await sendBridgeCommand(
        bridgeId,
        'audio.dsp.record_compare',
        { seconds: 10 },
        45,
      );
      renderRecording(result);
      if (typeof toast === 'function') toast('原声与处理后 WAV 已保存');
    } catch (error) {
      box.className = 'dsp-ab-result diagnosis bad';
      box.textContent = error.message;
      if (typeof toast === 'function') toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  function install() {
    const bridge = document.getElementById('dsp-bridge');
    const input = document.getElementById('dsp-input');
    const output = document.getElementById('dsp-output');
    const record = document.getElementById('dsp-record');
    if (!bridge || !input || !output || !record) {
      window.setTimeout(install, 200);
      return;
    }
    if (installed) return;
    installed = true;

    input.disabled = true;
    output.disabled = true;
    record.addEventListener('click', recordCompare, true);
    bridge.addEventListener('change', () => window.setTimeout(() => autoConfigure(false).catch(() => {}), 100));
    document.getElementById('dsp-refresh')?.addEventListener(
      'click',
      () => window.setTimeout(() => autoConfigure(true).catch(() => {}), 650),
    );
    window.addEventListener('aliver:tabchange', event => {
      if (event.detail?.name === 'voice-lab') {
        window.setTimeout(() => autoConfigure(false).catch(() => {}), 200);
      }
    });

    [350, 900, 1800].forEach(delay => {
      window.setTimeout(() => autoConfigure(delay === 900).catch(() => {}), delay);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
