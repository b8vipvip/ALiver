(() => {
  let busy = false;

  function escape(value) {
    return typeof escapeHtml === 'function'
      ? escapeHtml(String(value ?? ''))
      : String(value ?? '').replace(/[&<>"']/g, char => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[char]);
  }

  function selectedBridgeId() {
    const value = document.getElementById('audio-bridge')?.value || '';
    if (!value) throw new Error('请先选择在线 Bridge');
    return value;
  }

  function ensureStyle() {
    if (document.getElementById('aliver-audio-live-setup-style')) return;
    const style = document.createElement('style');
    style.id = 'aliver-audio-live-setup-style';
    style.textContent = `
      .audio-live-setup-panel { margin-top: 14px; }
      .audio-live-targets {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 10px;
        margin: 12px 0;
      }
      .audio-live-targets article {
        padding: 12px;
        border: 1px solid var(--border, #263241);
        border-radius: 10px;
        min-width: 0;
      }
      .audio-live-targets span,
      .audio-live-targets small { display: block; color: var(--muted, #94a3b8); }
      .audio-live-targets strong { display: block; margin: 5px 0; overflow-wrap: anywhere; }
      .audio-live-setup-panel details pre { max-height: 360px; overflow: auto; }
    `;
    document.head.appendChild(style);
  }

  function ensurePanel() {
    const intro = document.querySelector('#tab-audio .route-intro');
    if (!intro) return null;
    let panel = document.getElementById('audio-live-setup-panel');
    if (panel) return panel;

    const actions = intro.querySelector('.actions');
    if (actions && !document.getElementById('audio-live-auto')) {
      const button = document.createElement('button');
      button.id = 'audio-live-auto';
      button.type = 'button';
      button.textContent = '一键配置直播语音与口型';
      actions.prepend(button);
    }

    panel = document.createElement('article');
    panel.id = 'audio-live-setup-panel';
    panel.className = 'panel audio-live-setup-panel';
    panel.innerHTML = `
      <div class="section-title">
        <div>
          <h2>直播语音与 VTube Studio 口型联动</h2>
          <p class="hint">自动识别直通或实时 DSP 模式。DSP 启用时，直播伴侣和 VTube Studio 只允许使用 CABLE-B Output；原声 CABLE Output 仅供 DSP 捕获。</p>
        </div>
        <div class="actions">
          <span id="audio-live-badge" class="badge warn">未配置</span>
          <button id="audio-live-refresh" type="button" class="secondary">读取状态</button>
          <button id="audio-live-stop" type="button" class="danger">停止口型兜底</button>
        </div>
      </div>
      <div id="audio-live-diagnosis" class="diagnosis warn">点击“一键配置直播语音与口型”开始。</div>
      <div id="audio-live-targets" class="audio-live-targets"></div>
      <details>
        <summary>查看完整配置与验证结果</summary>
        <pre id="audio-live-json">尚未运行。</pre>
      </details>
    `;
    intro.insertAdjacentElement('afterend', panel);

    document.getElementById('audio-live-auto')?.addEventListener('click', () => {
      autoConfigure().catch(error => showError(error));
    });
    panel.querySelector('#audio-live-refresh')?.addEventListener('click', () => {
      refreshStatus(true).catch(error => showError(error));
    });
    panel.querySelector('#audio-live-stop')?.addEventListener('click', () => {
      stopSetup().catch(error => showError(error));
    });
    return panel;
  }

  function targetCard(label, value, note) {
    return `<article><span>${escape(label)}</span><strong>${escape(value || '未检测')}</strong><small>${escape(note)}</small></article>`;
  }

  function render(data) {
    ensurePanel();
    const badge = document.getElementById('audio-live-badge');
    const diagnosis = document.getElementById('audio-live-diagnosis');
    const targets = document.getElementById('audio-live-targets');
    const json = document.getElementById('audio-live-json');
    if (!badge || !diagnosis || !targets || !json) return;

    const instructions = data?.instructions || {};
    const native = data?.native_lipsync;
    const fallback = Boolean(data?.fallback_running || data?.api_mouth_fallback);
    const routeReady = Boolean(data?.route_ready);
    const dspMode = instructions.routing_mode === 'dsp_processed' || Boolean(instructions.dsp_selected);
    const duplicateRisk = Boolean(data?.duplicate_audio_risk);

    let kind = 'warn';
    let message = dspMode
      ? '实时 DSP 已启用；直播伴侣和 VTube Studio 必须只选择处理后的 CABLE-B Output。'
      : '当前为原声直通模式；直播伴侣和 VTube Studio 使用 CABLE Output。';
    let label = dspMode ? 'DSP 模式' : '直通模式';
    if (duplicateRisk) {
      kind = 'bad';
      label = '存在双声风险';
      message = 'DSP 已启用，但直播目标仍指向原声 CABLE Output。请重新一键配置并改用 CABLE-B Output。';
    } else if (routeReady && native?.passed) {
      kind = 'good';
      label = dspMode ? 'DSP 路由正常' : '原生口型正常';
      message = dspMode
        ? 'DSP 三线链路已就绪，VTube Studio 与直播伴侣目标均为 CABLE-B Output。'
        : '原声直通路由已就绪，VTube Studio 原生麦克风口型验证通过。';
    } else if (routeReady && fallback) {
      kind = 'good';
      label = dspMode ? 'DSP + API 口型' : 'API 口型兜底中';
      message = dspMode
        ? 'DSP 三线链路已就绪；VTube Studio 使用 CABLE-B Output，API 口型兜底已启动。'
        : '音频路由已就绪；VTube Studio 原生口型没有响应，已自动启用 GPT_OUT 音量驱动口型。';
    } else if (routeReady && !data?.session_id) {
      label = dspMode ? 'DSP 路由待验证' : '等待 VTube 会话';
      message = dspMode
        ? 'DSP 路由已选择 CABLE-B Output。启动 VTube Studio 会话后再次验证口型。'
        : '虚拟声卡已配置。启动 VTube Studio 数字人会话后再次点击一键配置。';
    } else if (data?.last_error) {
      kind = 'bad';
      label = '配置失败';
      message = data.last_error;
    }

    badge.textContent = label;
    badge.className = `badge ${kind}`;
    diagnosis.textContent = duplicateRisk ? message : (native?.diagnosis || message);
    diagnosis.className = `diagnosis ${kind}`;
    targets.innerHTML = [
      targetCard('Chrome / ChatGPT 输出', instructions.chrome_output, '原声进入标准 VB-CABLE'),
      targetCard(
        '直播伴侣主麦克风',
        instructions.douyin_microphone,
        dspMode ? 'DSP 模式只能选择处理后的录音端；不要同时添加 CABLE Output' : '原声直通录音端',
      ),
      targetCard(
        'VTube Studio 麦克风',
        instructions.vtube_microphone,
        dspMode ? '选择 CABLE-B Output 并开启 Use microphone' : '选择原声录音端并开启 Use microphone',
      ),
      targetCard('ChatGPT Live 麦克风', instructions.chatgpt_microphone, 'GPT_IN 独立虚拟麦克风'),
    ].join('');
    json.textContent = JSON.stringify(data || {}, null, 2);

    if (data?.scan && typeof renderAudioScan === 'function') renderAudioScan(data.scan);
  }

  function showError(error) {
    ensurePanel();
    const diagnosis = document.getElementById('audio-live-diagnosis');
    const badge = document.getElementById('audio-live-badge');
    if (diagnosis) {
      diagnosis.textContent = error.message;
      diagnosis.className = 'diagnosis bad';
    }
    if (badge) {
      badge.textContent = '配置失败';
      badge.className = 'badge bad';
    }
    if (typeof toast === 'function') toast(error.message, true);
  }

  async function autoConfigure() {
    if (busy) return;
    busy = true;
    const button = document.getElementById('audio-live-auto');
    const previous = button?.textContent || '';
    if (button) {
      button.disabled = true;
      button.textContent = '正在识别 DSP 并配置…';
    }
    const diagnosis = document.getElementById('audio-live-diagnosis');
    if (diagnosis) {
      diagnosis.textContent = '正在检测实时 DSP 状态、选择唯一直播输出并验证 VTube Studio 口型…';
      diagnosis.className = 'diagnosis warn';
    }
    try {
      const data = await sendBridgeCommand(
        selectedBridgeId(),
        'audio.live.auto_configure',
        { enable_api_fallback: true },
        90,
      );
      render(data);
      if (typeof toast === 'function') {
        const dspMode = data?.instructions?.routing_mode === 'dsp_processed';
        toast(dspMode ? 'DSP 直播输出已统一到 CABLE-B Output' : '原声直通与 VTube Studio 口型验证完成');
      }
      return data;
    } finally {
      busy = false;
      if (button) {
        button.disabled = false;
        button.textContent = previous || '一键配置直播语音与口型';
      }
    }
  }

  async function refreshStatus(showToast = false) {
    const data = await sendBridgeCommand(selectedBridgeId(), 'audio.live.status', {}, 20);
    render(data);
    if (showToast && typeof toast === 'function') toast('直播音频状态已刷新');
    return data;
  }

  async function stopSetup() {
    const data = await sendBridgeCommand(selectedBridgeId(), 'audio.live.stop', {}, 30);
    render(data);
    if (typeof toast === 'function') toast('ALiver 音量驱动口型兜底已停止');
    return data;
  }

  function start() {
    if (
      typeof sendBridgeCommand !== 'function'
      || !document.getElementById('tab-audio')
      || !ensurePanel()
    ) {
      window.setTimeout(start, 200);
      return;
    }
    ensureStyle();
  }

  window.runAliverLiveAudioSetup = autoConfigure;
  start();
})();
