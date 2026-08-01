(() => {
  const state = {
    installed: false,
    library: null,
    editingPresetId: null,
    dirty: false,
    busy: false,
  };

  const soundKeys = [
    'pitch_semitones',
    'tone_age',
    'low_cut_hz',
    'bass_db',
    'presence_db',
    'compressor_threshold_db',
    'compressor_ratio',
    'output_gain_db',
    'limiter_threshold_db',
  ];

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(value ?? ''));
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function selectedBridge() {
    const value = document.getElementById('dsp-bridge')?.value || '';
    if (!value) throw new Error('请先选择在线 Bridge。');
    return value;
  }

  function unitFor(key) {
    if (key === 'pitch_semitones') return ' st';
    if (key === 'low_cut_hz') return ' Hz';
    if (key.endsWith('_db') || key.includes('threshold_db')) return ' dB';
    if (key === 'compressor_ratio') return ':1';
    return '';
  }

  function setControlValues(values = {}) {
    soundKeys.forEach(key => {
      const input = document.getElementById(`dsp-${key}`);
      const output = document.getElementById(`dsp-${key}-value`);
      if (!input || values[key] === undefined) return;
      input.value = String(values[key]);
      if (output) output.textContent = `${input.value}${unitFor(key)}`;
    });
  }

  function collectValues(base = {}) {
    const values = { ...base };
    soundKeys.forEach(key => {
      const input = document.getElementById(`dsp-${key}`);
      if (input) values[key] = Number(input.value);
    });
    values.block_size = Number(document.getElementById('dsp-block-size')?.value || 1024);
    values.sample_rate = 48000;
    values.channels = 2;
    values.input_device_key = document.getElementById('dsp-input')?.value || '';
    values.output_device_key = document.getElementById('dsp-output')?.value || '';
    return values;
  }

  function setMessage(text, kind = 'warn') {
    const box = document.getElementById('dsp-preset-library-result');
    if (!box) return;
    box.className = `diagnosis ${kind}`;
    box.textContent = text;
  }

  function updateButtons() {
    const customSelected = Boolean(state.editingPresetId);
    const update = document.getElementById('dsp-preset-update');
    const remove = document.getElementById('dsp-preset-delete');
    if (update) update.disabled = !customSelected || state.busy;
    if (remove) remove.disabled = !customSelected || state.busy;
    const dirty = document.getElementById('dsp-preset-dirty');
    if (dirty) dirty.textContent = state.dirty ? '当前参数有未保存修改' : '';
  }

  function sortedMeta(kind) {
    return Object.values(state.library?.preset_meta || {})
      .filter(row => row.kind === kind)
      .sort((a, b) => Number(a.order || 9999) - Number(b.order || 9999) || String(a.name).localeCompare(String(b.name), 'zh-CN'));
  }

  function renderSelect(selectedId) {
    const select = document.getElementById('dsp-preset');
    if (!select || !state.library) return;
    const builtins = sortedMeta('builtin');
    const custom = sortedMeta('custom');
    select.innerHTML = `
      <optgroup label="内置声音">
        ${builtins.map(row => `<option value="${esc(row.id)}">${esc(row.name)}</option>`).join('')}
      </optgroup>
      ${custom.length ? `<optgroup label="我的声音">${custom.map(row => `<option value="${esc(row.id)}">${esc(row.name)}</option>`).join('')}</optgroup>` : ''}
      <option value="custom">自定义（未保存）</option>`;
    const requested = selectedId || select.dataset.lastPreset || 'original';
    select.value = state.library.preset_meta?.[requested] ? requested : requested === 'custom' ? 'custom' : 'original';
    select.dataset.lastPreset = select.value;
    const meta = state.library.preset_meta?.[select.value];
    state.editingPresetId = meta?.kind === 'custom' ? meta.id : null;
    const name = document.getElementById('dsp-preset-name');
    if (name && meta?.kind === 'custom') name.value = meta.name || '';
    renderDescription(meta);
    updateButtons();
  }

  function renderDescription(meta) {
    const box = document.getElementById('dsp-preset-description');
    if (!box) return;
    if (!meta) {
      box.textContent = '调整参数后填写名称，可保存为自己的声音。';
      return;
    }
    box.innerHTML = `<strong>${esc(meta.name)}</strong> · ${meta.kind === 'custom' ? '我的声音' : '内置声音'}<br>${esc(meta.description || '暂无说明')}`;
  }

  async function loadLibrary(preferredId = '') {
    if (typeof sendBridgeCommand !== 'function') return;
    const bridgeId = selectedBridge();
    const [library, status] = await Promise.all([
      sendBridgeCommand(bridgeId, 'audio.dsp.presets', {}, 15),
      sendBridgeCommand(bridgeId, 'audio.dsp.status', {}, 15),
    ]);
    state.library = library;
    renderSelect(preferredId || status.config?.preset || 'original');
    const path = document.getElementById('dsp-preset-storage');
    if (path) path.textContent = `已保存 ${Number(library.custom_count || 0)} 个自定义声音 · ${library.storage_path || ''}`;
  }

  async function applyPreset(presetId) {
    const meta = state.library?.preset_meta?.[presetId];
    const values = state.library?.presets?.[presetId];
    if (!meta || !values) {
      state.editingPresetId = null;
      state.dirty = true;
      renderDescription(null);
      updateButtons();
      return;
    }
    setControlValues(values);
    state.editingPresetId = meta.kind === 'custom' ? presetId : null;
    state.dirty = false;
    const name = document.getElementById('dsp-preset-name');
    if (name) name.value = meta.kind === 'custom' ? meta.name || '' : '';
    renderDescription(meta);
    updateButtons();
    const payload = collectValues(values);
    payload.preset = presetId;
    const result = await sendBridgeCommand(selectedBridge(), 'audio.dsp.configure', payload, 30);
    const saveState = document.getElementById('dsp-save-state');
    if (saveState) saveState.textContent = `已选择“${meta.name}” ${new Date().toLocaleTimeString()}`;
    if (result?.config?.preset) {
      document.getElementById('dsp-preset').dataset.lastPreset = result.config.preset;
    }
  }

  async function savePreset(updateExisting) {
    if (state.busy) return;
    const nameInput = document.getElementById('dsp-preset-name');
    const name = nameInput?.value.trim() || '';
    if (!name) {
      nameInput?.focus();
      throw new Error('请先填写声音名称。');
    }
    if (updateExisting && !state.editingPresetId) {
      throw new Error('请先选择一个“我的声音”，再执行覆盖更新。');
    }
    state.busy = true;
    updateButtons();
    setMessage(updateExisting ? '正在更新声音…' : '正在保存新声音…');
    try {
      const status = await sendBridgeCommand(selectedBridge(), 'audio.dsp.status', {}, 15);
      const values = collectValues(status.config || {});
      const result = await sendBridgeCommand(
        selectedBridge(),
        'audio.dsp.preset.save',
        {
          name,
          values,
          preset_id: updateExisting ? state.editingPresetId : null,
        },
        20,
      );
      state.library = result;
      const savedId = result.saved.id;
      state.editingPresetId = savedId;
      state.dirty = false;
      renderSelect(savedId);
      const configurePayload = collectValues(result.saved.values || values);
      configurePayload.preset = savedId;
      await sendBridgeCommand(selectedBridge(), 'audio.dsp.configure', configurePayload, 30);
      const path = document.getElementById('dsp-preset-storage');
      if (path) path.textContent = `已保存 ${Number(result.custom_count || 0)} 个自定义声音 · ${result.storage_path || ''}`;
      setMessage(`声音“${result.saved.name}”已保存，下次可直接从声音预设中选择。`, 'good');
      if (typeof toast === 'function') toast(`已保存声音：${result.saved.name}`);
    } finally {
      state.busy = false;
      updateButtons();
    }
  }

  async function deletePreset() {
    if (!state.editingPresetId) throw new Error('请先选择要删除的“我的声音”。');
    const meta = state.library?.preset_meta?.[state.editingPresetId];
    if (!window.confirm(`确定删除声音“${meta?.name || state.editingPresetId}”吗？`)) return;
    state.busy = true;
    updateButtons();
    try {
      const result = await sendBridgeCommand(
        selectedBridge(),
        'audio.dsp.preset.delete',
        { preset_id: state.editingPresetId },
        20,
      );
      state.library = result;
      state.editingPresetId = null;
      state.dirty = false;
      renderSelect('original');
      await applyPreset('original');
      const path = document.getElementById('dsp-preset-storage');
      if (path) path.textContent = `已保存 ${Number(result.custom_count || 0)} 个自定义声音 · ${result.storage_path || ''}`;
      setMessage('自定义声音已删除，当前已切换为原声整理。', 'good');
      if (typeof toast === 'function') toast('自定义声音已删除');
    } finally {
      state.busy = false;
      updateButtons();
    }
  }

  function installPanel() {
    if (state.installed) return true;
    const preset = document.getElementById('dsp-preset');
    const routeGrid = preset?.closest('.dsp-route-grid');
    const controlGrid = document.querySelector('#voice-lab-root .dsp-control-grid');
    if (!preset || !routeGrid || !controlGrid || typeof sendBridgeCommand !== 'function') return false;

    state.installed = true;
    const style = document.createElement('style');
    style.textContent = `
      .dsp-preset-library{margin-top:14px;border:1px solid var(--border,#263241);border-radius:12px;padding:14px;background:rgba(8,18,31,.28)}
      .dsp-preset-library-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
      .dsp-preset-save-row{display:grid;grid-template-columns:minmax(180px,1fr) auto auto auto;gap:8px;margin-top:12px;align-items:end}
      .dsp-preset-save-row label{margin:0}.dsp-preset-save-row button{white-space:nowrap}
      #dsp-preset-description{margin-top:10px;line-height:1.55}.dsp-preset-library small{display:block;margin-top:8px;color:var(--muted,#94a3b8);overflow-wrap:anywhere}
      @media(max-width:900px){.dsp-preset-save-row{grid-template-columns:1fr 1fr}.dsp-preset-save-row label{grid-column:1/-1}}
    `;
    document.head.appendChild(style);

    const panel = document.createElement('section');
    panel.className = 'dsp-preset-library';
    panel.innerHTML = `
      <div class="dsp-preset-library-head">
        <div><span class="page-kicker">VOICE LIBRARY</span><h3>声音预设库</h3></div>
        <span id="dsp-preset-dirty" class="hint"></span>
      </div>
      <div id="dsp-preset-description" class="diagnosis warn">正在读取声音库…</div>
      <div class="dsp-preset-save-row">
        <label>声音名称<input id="dsp-preset-name" maxlength="40" placeholder="例如：我的自然少女 01"></label>
        <button id="dsp-preset-save-new" type="button">另存为新声音</button>
        <button id="dsp-preset-update" type="button" class="secondary" disabled>覆盖当前声音</button>
        <button id="dsp-preset-delete" type="button" class="danger" disabled>删除</button>
      </div>
      <div id="dsp-preset-library-result" class="diagnosis warn" style="margin-top:10px">调整参数后可命名保存。</div>
      <small id="dsp-preset-storage"></small>`;
    controlGrid.parentElement.insertBefore(panel, controlGrid);

    preset.addEventListener('change', event => {
      event.stopImmediatePropagation();
      const presetId = preset.value;
      preset.dataset.lastPreset = presetId;
      applyPreset(presetId).catch(error => {
        setMessage(error.message, 'bad');
        if (typeof toast === 'function') toast(error.message, true);
      });
    }, true);

    soundKeys.forEach(key => {
      document.getElementById(`dsp-${key}`)?.addEventListener('input', () => {
        state.dirty = true;
        updateButtons();
      }, true);
    });

    document.getElementById('dsp-preset-save-new').addEventListener('click', () => {
      savePreset(false).catch(error => {
        setMessage(error.message, 'bad');
        if (typeof toast === 'function') toast(error.message, true);
      });
    });
    document.getElementById('dsp-preset-update').addEventListener('click', () => {
      savePreset(true).catch(error => {
        setMessage(error.message, 'bad');
        if (typeof toast === 'function') toast(error.message, true);
      });
    });
    document.getElementById('dsp-preset-delete').addEventListener('click', () => {
      deletePreset().catch(error => {
        setMessage(error.message, 'bad');
        if (typeof toast === 'function') toast(error.message, true);
      });
    });
    document.getElementById('dsp-bridge')?.addEventListener('change', () => {
      window.setTimeout(() => loadLibrary().catch(error => setMessage(error.message, 'bad')), 250);
    });
    document.getElementById('dsp-refresh')?.addEventListener('click', () => {
      window.setTimeout(() => loadLibrary().catch(error => setMessage(error.message, 'bad')), 900);
    });

    window.setTimeout(() => loadLibrary().catch(error => setMessage(error.message, 'bad')), 500);
    return true;
  }

  function start() {
    if (!installPanel()) window.setTimeout(start, 250);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
