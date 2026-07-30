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
  for (const [src, id] of [
    ['/static/diagnostics_zh.js?v=0.12.1', 'aliver-diagnostics-zh'],
    ['/static/provider_catalog.js?v=0.12.1', 'aliver-provider-catalog-script'],
    ['/static/auto_director_refresh_fix.js?v=0.12.1', 'aliver-auto-director-refresh-fix'],
  ]) {
    if (document.getElementById(id)) continue;
    const script = document.createElement('script');
    script.id = id;
    script.src = src;
    script.async = false;
    document.head.appendChild(script);
  }
})();
