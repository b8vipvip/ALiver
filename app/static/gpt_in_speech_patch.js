(() => {
  const original = document.getElementById('gpt-in-test');
  if (!original) return;

  const button = original.cloneNode(true);
  original.replaceWith(button);

  button.addEventListener('click', async () => {
    const initialText = button.textContent;
    button.disabled = true;
    button.textContent = '正在合成人声并发送…';
    try {
      const data = await sendBridgeCommand(
        selectedAudioBridge(),
        'audio.gpt_in.test',
        {
          text: '你好，ChatGPT。这是 ALiver 虚拟麦克风测试。听到这句话以后，请回答测试成功。 Hello ChatGPT, please reply test successful.',
        },
        60,
      );
      document.getElementById('gpt-in-test-json').textContent = JSON.stringify(data, null, 2);
      toast(
        `人声已送入 GPT_IN；等待 ChatGPT 回复。麦克风应选择：${data.microphone_hint || '匹配虚拟麦克风'}`,
      );
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = initialText;
    }
  });
})();

(() => {
  const version = '0.16.1';

  document.documentElement.classList.add('aliver-shell-booting');
  if (!document.getElementById('aliver-shell-boot-style')) {
    const style = document.createElement('style');
    style.id = 'aliver-shell-boot-style';
    style.textContent = `
      html.aliver-shell-booting body::before {
        content: '正在加载 ALiver 控制台…';
        position: fixed;
        inset: 0;
        z-index: 99999;
        display: grid;
        place-items: center;
        background: #07101b;
        color: #dbeafe;
        font: 600 16px/1.5 system-ui, sans-serif;
      }
      html.aliver-shell-booting .topbar,
      html.aliver-shell-booting main { visibility: hidden !important; }
    `;
    document.head.appendChild(style);
  }

  const assets = [
    ['/static/request_timeout_patch.js', 'aliver-request-timeout-patch'],
    ['/static/console_shell_v3.js', 'aliver-console-shell-v3'],
    ['/static/console_layout_v2.js', 'aliver-console-layout-v2'],
    ['/static/live_debug_validation_v2.js', 'aliver-live-debug-validation-v2'],
    ['/static/live_debug_recovery_ui.js', 'aliver-live-debug-recovery-ui'],
    ['/static/wgc_hwnd_ui_patch.js', 'aliver-wgc-hwnd-ui-patch'],
    ['/static/audio_live_setup.js', 'aliver-audio-live-setup'],
    ['/static/local_time_patch.js', 'aliver-local-time-patch'],
    ['/static/diagnostics_zh.js', 'aliver-diagnostics-zh'],
    ['/static/provider_catalog.js', 'aliver-provider-catalog-script'],
    ['/static/auto_director_refresh_fix.js', 'aliver-auto-director-refresh-fix'],
    ['/static/live_run_console.js', 'aliver-live-run-console'],
    ['/static/native_voice_lab_v2.js', 'aliver-native-voice-lab-v2'],
    ['/static/realtime_voice_dsp_ui_patch.js', 'aliver-realtime-voice-dsp-ui-patch'],
    ['/static/dsp_doctor_feedback_patch.js', 'aliver-dsp-doctor-feedback-patch'],
    ['/static/console_refinement_v4.js', 'aliver-console-refinement-v4'],
  ];

  for (const [src, id] of assets) {
    if (document.getElementById(id)) continue;
    const script = document.createElement('script');
    script.id = id;
    script.src = `${src}?v=${version}`;
    script.async = false;
    script.dataset.aliverVersion = version;
    script.addEventListener('error', () => {
      console.error(`ALiver 前端模块加载失败：${script.src}`);
      document.documentElement.classList.remove('aliver-shell-booting');
      if (typeof toast === 'function') toast(`前端模块加载失败：${src}`, true);
    });
    document.body.appendChild(script);
  }
})();
