(() => {
  const state = { catalog: null, extensions: [], bridges: [], profile: null, mounted: false, busy: false };

  function markup() {
    return `
      <header class="ops-page-heading">
        <div><span class="page-kicker">VOICE LAB</span><h2>语音音色与表达风格</h2>
        <p>原生模式保留 ChatGPT Voice 的低延迟对话并调整语气风格；API TTS 模式使用指定语音模型和音色重新朗读完整回答。</p></div>
        <div class="actions"><button id="voice-refresh" type="button" class="secondary">刷新</button></div>
      </header>
      <section class="ops-grid">
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">PROFILE</span><h2>直播语音配置</h2></div>
          <span id="voice-profile-badge" class="badge warn">未加载</span></div>
          <label>目标 ChatGPT 导演扩展<select id="voice-extension"><option value="">请选择在线扩展</option></select></label>
          <label class="check-row"><input id="voice-enabled" type="checkbox">启用本配置，并自动应用到自动导演口播</label>
          <div class="voice-mode-switch">
            <label><input type="radio" name="voice-mode" value="chatgpt_live" checked><strong>ChatGPT Live 原生语音</strong>
            <small>延迟最低；ALiver 调整语气、节奏和表达方式，具体内置音色需在 ChatGPT 中选择。</small></label>
            <label><input type="radio" name="voice-mode" value="api_tts"><strong>ALiver API TTS</strong>
            <small>捕获完整文字回答，再用指定模型和音色输出到 GPT_OUT；第一版为整句生成。</small></label>
          </div>
          <div class="voice-form-grid">
            <label>风格预设<select id="voice-preset"></select></label>
            <label>推荐 ChatGPT 内置音色<select id="voice-native"></select></label>
            <label class="wide">自定义说话方式<textarea id="voice-instruction" rows="5" placeholder="例如：明亮、甜美、轻快，声音偏年轻，咬字清楚，不要过度卖萌。"></textarea></label>
            <label class="check-row wide"><input id="voice-auto-style" type="checkbox" checked>每条自动导演指令都附带此语音呈现要求</label>
          </div>
          <div id="voice-native-notice" class="voice-notice"></div>
          <div id="voice-api-fields" class="voice-form-grid" hidden>
            <label>执行 Bridge<select id="voice-bridge"><option value="">自动选择在线 Bridge</option></select></label>
            <label>TTS API Base URL<input id="voice-api-base" placeholder="例如 https://api.openai.com/v1"></label>
            <label>TTS 模型<input id="voice-model" value="gpt-4o-mini-tts"></label>
            <label>TTS 音色<select id="voice-tts-voice"></select></label>
            <label>语速<input id="voice-speed" type="number" min="0.25" max="4" step="0.05" value="1.03"></label>
            <label>API Key<input id="voice-api-key" type="password" placeholder="留空表示保留已保存密钥"></label>
            <label class="check-row"><input id="voice-clear-key" type="checkbox">清除已保存 API Key</label>
            <div class="voice-notice wide">API TTS 第一版需要把 ChatGPT 切换为文字对话，或手动静音 ChatGPT 标签页，否则会听到原声和合成音重叠。完整回答生成后才播放，延迟会高于原生 Voice。</div>
          </div>
          <div class="ops-toolbar" style="margin-top:14px">
            <button id="voice-save" type="button">保存配置</button>
            <button id="voice-apply" type="button" class="secondary">应用到当前对话</button>
            <span class="spacer"></span>
            <span id="voice-save-state" class="hint"></span>
          </div>
        </article>
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">PREVIEW</span><h2>效果预览与测试</h2></div></div>
          <div class="voice-preview-card">
            <span id="voice-preview-name" class="badge good">自然少女</span>
            <blockquote id="voice-preview-text">正在读取语音风格。</blockquote>
            <div class="meta">原生模式会把表达要求发送到绑定的 ChatGPT 对话；API TTS 模式会直接合成下方测试文本。</div>
          </div>
          <label style="margin-top:14px">测试文本<textarea id="voice-test-text" rows="5">你好呀，欢迎来到直播间。今天我们聊点轻松有趣的 AI 和生活故事，你现在是在休息，还是一边忙一边听呢？</textarea></label>
          <div class="ops-toolbar">
            <button id="voice-test" type="button">播放 API TTS 测试</button>
            <button id="voice-copy-instruction" type="button" class="secondary">复制风格描述</button>
          </div>
          <div id="voice-result" class="diagnosis warn">尚未测试。</div>
          <div class="voice-notice"><strong>关于“小女孩声音”：</strong>ALiver 使用的是“甜美小女孩感”风格，不模仿任何具体真人。原生 ChatGPT Voice 的底层音色仍由 ChatGPT 的语音设置决定。</div>
        </article>
      </section>`;
  }

  function mount() {
    const root = document.getElementById('voice-lab-root');
    if (!root || state.mounted) return Boolean(root);
    state.mounted = true;
    root.innerHTML = markup();
    document.getElementById('voice-refresh').addEventListener('click', () => refresh(true));
    document.getElementById('voice-extension').addEventListener('change', loadProfile);
    document.querySelectorAll('input[name="voice-mode"]').forEach(input => input.addEventListener('change', renderMode));
    document.getElementById('voice-preset').addEventListener('change', applyPreset);
    document.getElementById('voice-instruction').addEventListener('input', renderPreview);
    document.getElementById('voice-native').addEventListener('change', renderPreview);
    document.getElementById('voice-tts-voice').addEventListener('change', renderPreview);
    document.getElementById('voice-save').addEventListener('click', saveProfile);
    document.getElementById('voice-apply').addEventListener('click', applyProfile);
    document.getElementById('voice-test').addEventListener('click', testVoice);
    document.getElementById('voice-copy-instruction').addEventListener('click', copyInstruction);
    return true;
  }

  function selectedMode() {
    return document.querySelector('input[name="voice-mode"]:checked')?.value || 'chatgpt_live';
  }

  function renderMode() {
    const apiMode = selectedMode() === 'api_tts';
    document.getElementById('voice-api-fields').hidden = !apiMode;
    document.getElementById('voice-test').disabled = !apiMode;
    const notice = document.getElementById('voice-native-notice');
    notice.innerHTML = apiMode
      ? '<strong>API TTS：</strong>实际音色由所选 TTS voice 决定；建议先测试再正式开播。'
      : '<strong>原生 ChatGPT Voice：</strong>ALiver 能持续调整说话方式，但不能通过公开控制接口直接替你切换 ChatGPT 内置音色。请在 ChatGPT Voice 设置中选用下方推荐音色。';
    renderPreview();
  }

  function fillSelect(select, values, selected) {
    select.innerHTML = values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    if (values.includes(selected)) select.value = selected;
  }

  function fillCatalog() {
    const presets = state.catalog?.style_presets || {};
    document.getElementById('voice-preset').innerHTML = Object.entries(presets)
      .map(([key, value]) => `<option value="${escapeHtml(key)}">${escapeHtml(value.name)}</option>`).join('');
    fillSelect(document.getElementById('voice-native'), state.catalog?.chatgpt_native_voices || [], 'Maple');
    fillSelect(document.getElementById('voice-tts-voice'), state.catalog?.tts_voices || [], 'shimmer');
  }

  function fillTargets() {
    const extension = document.getElementById('voice-extension');
    const previous = extension.value;
    extension.innerHTML = '<option value="">请选择导演扩展</option>' + state.extensions.map(item =>
      `<option value="${item.id}">${escapeHtml(item.name)} · ${item.connected ? '在线' : '离线'} · ${escapeHtml(item.version)}</option>`
    ).join('');
    if (state.extensions.some(item => item.id === previous)) extension.value = previous;
    else if (state.extensions.length === 1) extension.value = state.extensions[0].id;

    const bridge = document.getElementById('voice-bridge');
    const oldBridge = bridge.value;
    bridge.innerHTML = '<option value="">自动选择在线 Bridge</option>' + state.bridges.map(item =>
      `<option value="${item.id}">${escapeHtml(item.name)} · ${item.connected ? '在线' : '离线'} · ${escapeHtml(item.version)}</option>`
    ).join('');
    if (state.bridges.some(item => item.id === oldBridge)) bridge.value = oldBridge;
  }

  function populateProfile(profile) {
    state.profile = profile;
    document.getElementById('voice-enabled').checked = Boolean(profile.enabled);
    const mode = document.querySelector(`input[name="voice-mode"][value="${profile.mode}"]`);
    if (mode) mode.checked = true;
    document.getElementById('voice-preset').value = profile.style_preset || 'sweet_young';
    document.getElementById('voice-native').value = profile.native_voice || 'Maple';
    document.getElementById('voice-instruction').value = profile.style_instruction || '';
    document.getElementById('voice-auto-style').checked = profile.auto_apply_style !== false;
    document.getElementById('voice-bridge').value = profile.bridge_id || '';
    document.getElementById('voice-api-base').value = profile.tts_api_base_url || '';
    document.getElementById('voice-model').value = profile.tts_model || 'gpt-4o-mini-tts';
    document.getElementById('voice-tts-voice').value = profile.tts_voice || 'shimmer';
    document.getElementById('voice-speed').value = profile.tts_speed || 1.03;
    document.getElementById('voice-api-key').value = '';
    document.getElementById('voice-api-key').placeholder = profile.credential_keys?.includes('api_key')
      ? '已保存密钥；留空保持不变'
      : '请输入 API Key';
    document.getElementById('voice-clear-key').checked = false;
    const badge = document.getElementById('voice-profile-badge');
    badge.textContent = profile.enabled ? (profile.mode === 'api_tts' ? 'API TTS 已启用' : '原生语音风格已启用') : '未启用';
    badge.className = `badge ${profile.enabled ? 'good' : 'warn'}`;
    renderMode();
  }

  function applyPreset() {
    const key = document.getElementById('voice-preset').value;
    const preset = state.catalog?.style_presets?.[key];
    if (preset) document.getElementById('voice-instruction').value = preset.instruction || preset.description || '';
    renderPreview();
  }

  function renderPreview() {
    const presetKey = document.getElementById('voice-preset').value;
    const preset = state.catalog?.style_presets?.[presetKey] || {};
    const instruction = document.getElementById('voice-instruction').value.trim() || preset.instruction || '';
    const mode = selectedMode();
    const voice = mode === 'api_tts'
      ? document.getElementById('voice-tts-voice').value
      : document.getElementById('voice-native').value;
    document.getElementById('voice-preview-name').textContent = `${preset.name || '自定义风格'} · ${voice || '未选音色'}`;
    document.getElementById('voice-preview-text').textContent = instruction || '尚未填写语音风格描述。';
  }

  async function refresh(showToast = false) {
    if (state.busy || !mount()) return;
    state.busy = true;
    try {
      [state.catalog, state.extensions, state.bridges] = await Promise.all([
        api('/api/voice/catalog'),
        api('/api/director/extensions'),
        api('/api/bridges'),
      ]);
      fillCatalog();
      fillTargets();
      await loadProfile();
      if (showToast) toast('语音实验室已刷新');
    } catch (error) {
      document.getElementById('voice-result').className = 'diagnosis bad';
      document.getElementById('voice-result').textContent = error.message;
      if (showToast) toast(error.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function loadProfile() {
    const extensionId = document.getElementById('voice-extension').value;
    if (!extensionId) {
      state.profile = null;
      return;
    }
    try {
      populateProfile(await api(`/api/voice/profiles/${encodeURIComponent(extensionId)}`));
    } catch (error) { toast(error.message, true); }
  }

  function payload() {
    return {
      enabled: document.getElementById('voice-enabled').checked,
      mode: selectedMode(),
      bridge_id: document.getElementById('voice-bridge').value || null,
      style_preset: document.getElementById('voice-preset').value,
      native_voice: document.getElementById('voice-native').value,
      style_instruction: document.getElementById('voice-instruction').value.trim(),
      auto_apply_style: document.getElementById('voice-auto-style').checked,
      auto_mute_chatgpt_tab: false,
      tts_api_base_url: document.getElementById('voice-api-base').value.trim() || null,
      tts_model: document.getElementById('voice-model').value.trim(),
      tts_voice: document.getElementById('voice-tts-voice').value,
      tts_speed: Number(document.getElementById('voice-speed').value || 1),
      api_key: document.getElementById('voice-api-key').value.trim() || null,
      clear_api_key: document.getElementById('voice-clear-key').checked,
    };
  }

  async function saveProfile() {
    const extensionId = document.getElementById('voice-extension').value;
    if (!extensionId) return toast('请先选择导演扩展', true);
    const button = document.getElementById('voice-save');
    button.disabled = true;
    try {
      populateProfile(await api(`/api/voice/profiles/${encodeURIComponent(extensionId)}`, {
        method: 'PUT', body: JSON.stringify(payload()),
      }));
      document.getElementById('voice-save-state').textContent = `已保存 ${new Date().toLocaleTimeString()}`;
      toast('语音配置已保存');
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }

  async function applyProfile() {
    const extensionId = document.getElementById('voice-extension').value;
    if (!extensionId) return toast('请先选择导演扩展', true);
    try {
      await saveProfile();
      const result = await api(`/api/voice/profiles/${encodeURIComponent(extensionId)}/apply`, { method: 'POST' });
      document.getElementById('voice-result').className = 'diagnosis good';
      document.getElementById('voice-result').textContent = result.message;
      toast('语音风格已应用到当前 ChatGPT 对话');
    } catch (error) { toast(error.message, true); }
  }

  async function testVoice() {
    const extensionId = document.getElementById('voice-extension').value;
    if (!extensionId) return toast('请先选择导演扩展', true);
    if (selectedMode() !== 'api_tts') return toast('播放测试仅适用于 API TTS 模式', true);
    const button = document.getElementById('voice-test');
    button.disabled = true;
    document.getElementById('voice-result').className = 'diagnosis warn';
    document.getElementById('voice-result').textContent = '正在调用 TTS 接口并输出到 GPT_OUT…';
    try {
      await saveProfile();
      const result = await api(`/api/voice/profiles/${encodeURIComponent(extensionId)}/test`, {
        method: 'POST',
        body: JSON.stringify({ text: document.getElementById('voice-test-text').value.trim() }),
      });
      document.getElementById('voice-result').className = 'diagnosis good';
      document.getElementById('voice-result').textContent = `测试音已播放：${result.characters} 字，生成耗时 ${result.synthesis_ms} ms。`;
    } catch (error) {
      document.getElementById('voice-result').className = 'diagnosis bad';
      document.getElementById('voice-result').textContent = error.message;
    } finally { button.disabled = false; }
  }

  async function copyInstruction() {
    try {
      await navigator.clipboard.writeText(document.getElementById('voice-instruction').value.trim());
      toast('语音风格描述已复制');
    } catch (error) { toast(error.message, true); }
  }

  function start() {
    if (!mount()) {
      const observer = new MutationObserver(() => {
        if (mount()) observer.disconnect();
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    setTimeout(() => refresh(), 1100);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
