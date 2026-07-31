(() => {
  const state = { mounted: false, busy: false, catalog: null, extensions: [], profile: null };
  const sliders = [
    ['pitch', '声线高低听感', '0 低沉 · 100 明亮偏高'],
    ['pace', '语速', '75 慢 · 100 自然 · 135 快'],
    ['sweetness', '甜美感', '自然亲切，不等同于模仿真人'],
    ['brightness', '明亮度', '柔和暗色 · 清爽通透'],
    ['energy', '元气与能量', '放松克制 · 活泼有回应'],
    ['warmth', '温暖陪伴感', '清薄 · 温柔包裹'],
    ['clarity', '咬字清晰度', '口语自然 · 清楚利落'],
    ['expressiveness', '情绪表现力', '克制 · 丰富但不过度'],
    ['pause', '停顿与呼吸感', '紧凑 · 松弛有停顿'],
  ];
  const presetTuning = {
    sweet_young: { pitch: 72, pace: 105, sweetness: 82, brightness: 78, energy: 64, warmth: 46, clarity: 84, expressiveness: 70, pause: 38 },
    natural_girl: { pitch: 62, pace: 102, sweetness: 58, brightness: 66, energy: 52, warmth: 52, clarity: 78, expressiveness: 58, pause: 46 },
    energetic: { pitch: 68, pace: 112, sweetness: 62, brightness: 78, energy: 84, warmth: 38, clarity: 82, expressiveness: 78, pause: 26 },
    gentle: { pitch: 54, pace: 92, sweetness: 54, brightness: 48, energy: 32, warmth: 82, clarity: 72, expressiveness: 46, pause: 70 },
    host: { pitch: 56, pace: 104, sweetness: 42, brightness: 62, energy: 62, warmth: 50, clarity: 90, expressiveness: 60, pause: 42 },
    calm: { pitch: 42, pace: 94, sweetness: 24, brightness: 44, energy: 34, warmth: 64, clarity: 88, expressiveness: 36, pause: 62 },
  };

  function sliderMarkup([key, title, hint]) {
    const min = key === 'pace' ? 75 : 0;
    const max = key === 'pace' ? 135 : 100;
    return `<label class="native-slider">
      <span class="native-slider-head"><strong>${title}</strong><output id="native-${key}-value">0</output></span>
      <input id="native-${key}" type="range" min="${min}" max="${max}" step="1">
      <small class="hint">${hint}</small>
    </label>`;
  }

  function markup() {
    return `
      <header class="ops-page-heading">
        <div><span class="page-kicker">NATIVE VOICE LAB</span><h2>ChatGPT 原生语音调音台</h2>
        <p>不调用第二路 TTS，不等待整句合成。ALiver 把声线高低听感、语速、甜美度和情绪等参数转换成持续的 Voice 表达要求。</p></div>
        <div class="actions"><button id="native-voice-refresh" type="button" class="secondary">刷新</button></div>
      </header>
      <section class="native-voice-grid">
        <article class="panel">
          <div class="section-title"><div><span class="page-kicker">PROFILE</span><h2>原生语音参数</h2></div>
          <span id="native-voice-badge" class="badge warn">未加载</span></div>
          <label>目标 ChatGPT 导演扩展<select id="native-extension"><option value="">请选择在线扩展</option></select></label>
          <label class="check-row"><input id="native-enabled" type="checkbox">启用，并自动应用到自动导演口播</label>
          <div class="voice-form-grid" style="margin-top:12px">
            <label>风格预设<select id="native-preset"></select></label>
            <label>推荐 ChatGPT 内置音色<select id="native-voice"></select></label>
            <label class="wide">补充要求<textarea id="native-custom" rows="4" placeholder="例如：声音自然一点，不要过度卖萌；回答时带一点笑意。"></textarea></label>
            <label class="check-row wide"><input id="native-auto-apply" type="checkbox" checked>每条自动导演指令都附带当前语音参数</label>
          </div>
          <div class="native-voice-control-grid" style="margin-top:14px">
            ${sliders.map(sliderMarkup).join('')}
          </div>
          <div class="ops-toolbar" style="margin-top:16px">
            <button id="native-save" type="button">保存参数</button>
            <button id="native-apply" type="button" class="secondary">保存并应用到当前对话</button>
            <button id="native-test" type="button" class="secondary">发送原生语音测试</button>
            <span class="spacer"></span><span id="native-save-state" class="hint"></span>
          </div>
        </article>
        <article class="panel native-voice-preview">
          <div class="section-title"><div><span class="page-kicker">PREVIEW</span><h2>参数解释与测试</h2></div></div>
          <div class="voice-preview-card">
            <span id="native-preview-name" class="badge good">读取中</span>
            <blockquote id="native-preview-text">正在读取语音参数。</blockquote>
          </div>
          <label style="margin-top:14px">测试口播<textarea id="native-test-text" rows="5">你好呀，欢迎来到直播间。今天我们轻松聊聊 AI 和生活趣事，你现在是在休息，还是一边忙一边听呢？</textarea></label>
          <div id="native-result" class="diagnosis warn">尚未发送测试。</div>
          <div class="native-voice-fact">
            <strong>工作方式：</strong>本页不生成 TTS，也不会增加整句合成延迟。ChatGPT Voice 可以根据要求改变语气、节奏和表达风格，但网页端目前没有公开的精确音高或播放速度旋钮，因此这里的数值代表目标听感，不是强制 DSP 数值。
          </div>
        </article>
      </section>`;
  }

  function values() {
    const result = {};
    sliders.forEach(([key]) => { result[key] = Number(document.getElementById(`native-${key}`).value); });
    return result;
  }

  function setValues(tuning) {
    sliders.forEach(([key]) => {
      const input = document.getElementById(`native-${key}`);
      const fallback = state.catalog?.default_tuning?.[key] ?? (key === 'pace' ? 100 : 50);
      input.value = Number(tuning?.[key] ?? fallback);
      document.getElementById(`native-${key}-value`).textContent = key === 'pace' ? `${input.value}%` : input.value;
    });
  }

  function level(value, low, medium, high) {
    return value < 35 ? low : value > 68 ? high : medium;
  }

  function previewInstruction() {
    const tuning = values();
    const custom = document.getElementById('native-custom').value.trim();
    const pitch = level(tuning.pitch, '声线听感稍低、放松', '声线自然中高', '声线明亮偏高、年轻，但不要尖细');
    const sweetness = level(tuning.sweetness, '减少甜腻感', '自然亲切、略带甜感', '甜美轻巧，但不要嗲声嗲气');
    const brightness = level(tuning.brightness, '音色柔和偏暗', '音色清爽自然', '音色明亮通透');
    const energy = level(tuning.energy, '能量放松克制', '自然聊天能量', '更有元气和回应感');
    const warmth = level(tuning.warmth, '减少厚重感', '适度温暖', '增加温柔陪伴感');
    const clarity = level(tuning.clarity, '咬字更口语', '咬字自然清楚', '咬字清晰利落');
    const expression = level(tuning.expressiveness, '情绪克制', '情绪自然变化', '加强笑意、惊喜和重点变化');
    const pause = level(tuning.pause, '衔接紧凑', '停顿自然', '增加短停顿和呼吸感');
    const pace = tuning.pace <= 90 ? '语速明显偏慢' : tuning.pace <= 99 ? '语速稍慢' : tuning.pace <= 108 ? '语速自然偏轻快' : tuning.pace <= 118 ? '语速较快但保持清楚' : '语速快速紧凑但不要吞字';
    return [custom, `${pitch}；${sweetness}；${brightness}；${energy}；${warmth}；${clarity}；${expression}；${pause}；${pace}。`]
      .filter(Boolean).join('\n');
  }

  function renderPreview() {
    sliders.forEach(([key]) => {
      const input = document.getElementById(`native-${key}`);
      document.getElementById(`native-${key}-value`).textContent = key === 'pace' ? `${input.value}%` : input.value;
    });
    const preset = state.catalog?.style_presets?.[document.getElementById('native-preset').value];
    const voice = document.getElementById('native-voice').value;
    document.getElementById('native-preview-name').textContent = `${preset?.name || '自定义'} · 推荐 ${voice || '未选择'}`;
    document.getElementById('native-preview-text').textContent = previewInstruction();
  }

  function fillCatalog() {
    const presets = state.catalog?.style_presets || {};
    document.getElementById('native-preset').innerHTML = Object.entries(presets)
      .map(([key, item]) => `<option value="${escapeHtml(key)}">${escapeHtml(item.name)}</option>`).join('');
    document.getElementById('native-voice').innerHTML = (state.catalog?.chatgpt_native_voices || [])
      .map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
  }

  function fillExtensions() {
    const select = document.getElementById('native-extension');
    const previous = select.value;
    select.innerHTML = '<option value="">请选择导演扩展</option>' + state.extensions.map(item =>
      `<option value="${item.id}">${escapeHtml(item.name)} · ${item.connected ? '在线' : '离线'} · ${escapeHtml(item.version)}</option>`
    ).join('');
    if (state.extensions.some(item => item.id === previous)) select.value = previous;
    else if (state.extensions.length === 1) select.value = state.extensions[0].id;
  }

  function populate(profile) {
    state.profile = profile;
    document.getElementById('native-enabled').checked = Boolean(profile.enabled);
    document.getElementById('native-preset').value = profile.style_preset || 'sweet_young';
    document.getElementById('native-voice').value = profile.native_voice || 'Maple';
    document.getElementById('native-custom').value = profile.style_instruction || '';
    document.getElementById('native-auto-apply').checked = profile.auto_apply_style !== false;
    setValues(profile.native_tuning || state.catalog?.default_tuning || {});
    const badge = document.getElementById('native-voice-badge');
    badge.textContent = profile.enabled ? '原生调音已启用' : '未启用';
    badge.className = `badge ${profile.enabled ? 'good' : 'warn'}`;
    renderPreview();
  }

  function payload() {
    return {
      enabled: document.getElementById('native-enabled').checked,
      style_preset: document.getElementById('native-preset').value,
      native_voice: document.getElementById('native-voice').value,
      style_instruction: document.getElementById('native-custom').value.trim(),
      auto_apply_style: document.getElementById('native-auto-apply').checked,
      native_tuning: values(),
    };
  }

  async function loadProfile() {
    const extensionId = document.getElementById('native-extension').value;
    if (!extensionId) return;
    populate(await api(`/api/native-voice/profiles/${encodeURIComponent(extensionId)}`));
  }

  async function save(showToast = true) {
    const extensionId = document.getElementById('native-extension').value;
    if (!extensionId) throw new Error('请先选择导演扩展');
    const profile = await api(`/api/native-voice/profiles/${encodeURIComponent(extensionId)}`, {
      method: 'PUT', body: JSON.stringify(payload()),
    });
    populate(profile);
    document.getElementById('native-save-state').textContent = `已保存 ${new Date().toLocaleTimeString()}`;
    if (showToast) toast('原生语音参数已保存');
    return profile;
  }

  async function apply() {
    const extensionId = document.getElementById('native-extension').value;
    await save(false);
    const result = await api(`/api/native-voice/profiles/${encodeURIComponent(extensionId)}/apply`, { method: 'POST' });
    document.getElementById('native-result').className = 'diagnosis good';
    document.getElementById('native-result').textContent = result.message;
    toast('原生语音参数已应用到当前对话');
  }

  async function test() {
    const extensionId = document.getElementById('native-extension').value;
    await save(false);
    const result = await api(`/api/native-voice/profiles/${encodeURIComponent(extensionId)}/test`, {
      method: 'POST', body: JSON.stringify({ text: document.getElementById('native-test-text').value }),
    });
    document.getElementById('native-result').className = 'diagnosis good';
    document.getElementById('native-result').textContent = result.message;
    toast('原生语音测试已发送');
  }

  async function refresh(showToast = false) {
    if (state.busy) return;
    state.busy = true;
    try {
      [state.catalog, state.extensions] = await Promise.all([
        api('/api/native-voice/catalog'), api('/api/director/extensions'),
      ]);
      fillCatalog();
      fillExtensions();
      await loadProfile();
      if (showToast) toast('原生语音调音台已刷新');
    } catch (error) {
      document.getElementById('native-result').className = 'diagnosis bad';
      document.getElementById('native-result').textContent = error.message;
      if (showToast) toast(error.message, true);
    } finally { state.busy = false; }
  }

  function mount() {
    const root = document.getElementById('voice-lab-root');
    if (!root || state.mounted) return false;
    state.mounted = true;
    root.innerHTML = markup();
    document.getElementById('native-voice-refresh').addEventListener('click', () => refresh(true));
    document.getElementById('native-extension').addEventListener('change', () => loadProfile().catch(error => toast(error.message, true)));
    document.getElementById('native-preset').addEventListener('change', () => {
      const key = document.getElementById('native-preset').value;
      setValues(presetTuning[key] || state.catalog?.default_tuning || {});
      const preset = state.catalog?.style_presets?.[key];
      if (preset && !document.getElementById('native-custom').value.trim()) document.getElementById('native-custom').value = preset.instruction || '';
      renderPreview();
    });
    document.getElementById('native-voice').addEventListener('change', renderPreview);
    document.getElementById('native-custom').addEventListener('input', renderPreview);
    sliders.forEach(([key]) => document.getElementById(`native-${key}`).addEventListener('input', renderPreview));
    document.getElementById('native-save').addEventListener('click', () => save().catch(error => toast(error.message, true)));
    document.getElementById('native-apply').addEventListener('click', () => apply().catch(error => toast(error.message, true)));
    document.getElementById('native-test').addEventListener('click', () => test().catch(error => toast(error.message, true)));
    refresh().catch(() => {});
    return true;
  }

  function start() {
    if (mount()) return;
    const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
