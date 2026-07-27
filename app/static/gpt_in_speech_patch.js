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
  const form = document.getElementById('provider-form');
  const typeSelect = form?.querySelector('[name="provider_type"]');
  if (!form || !typeSelect || [...typeSelect.options].some(option => option.value === 'simli')) return;

  typeSelect.insertAdjacentHTML('beforeend', '<option value="simli">Simli（推荐）</option>');
  const helper = document.createElement('button');
  helper.type = 'button';
  helper.className = 'secondary';
  helper.textContent = '填入 Simli 推荐模板';
  helper.addEventListener('click', () => {
    typeSelect.value = 'simli';
    form.querySelector('[name="name"]').value = 'Simli Realtime';
    form.querySelector('[name="api_base_url"]').value = 'https://api.simli.ai';
    form.querySelector('[name="credentials"]').value = JSON.stringify({ api_key: '' }, null, 2);
    form.querySelector('[name="settings"]').value = JSON.stringify({
      face_id: '',
      transport: 'livekit',
      model: 'fasttalk',
      handle_silence: true,
      max_session_length: 3600,
      max_idle_time: 300,
      window_title: 'ALiver Simli Avatar',
      window_size: [720, 720],
      always_on_top: false,
      play_return_audio: true,
    }, null, 2);
    toast('已填入 Simli 模板。请填写 api_key 和 face_id 后保存。');
  });
  form.querySelector('button[type="submit"]').before(helper);

  const bridgeSelect = document.getElementById('session-bridge');
  const bridgeLabel = bridgeSelect?.closest('label');
  if (bridgeLabel?.firstChild) bridgeLabel.firstChild.textContent = 'Bridge（Simli / LiveAvatar 必选）';
})();
