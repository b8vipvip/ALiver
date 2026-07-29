(() => {
  const form = document.getElementById('provider-form');
  const typeSelect = form?.querySelector('[name="provider_type"]');
  const submit = form?.querySelector('button[type="submit"]');
  if (!form || !typeSelect || !submit || document.getElementById('aliver-provider-catalog')) return;

  const catalog = {
    tencent_digital_human: {
      label: '腾讯云智能数智人（预留适配）',
      name: 'Tencent Digital Human',
      apiBaseUrl: '',
      credentials: { app_key: '', access_token: '' },
      settings: {
        virtualman_project_id: '',
        asset_virtualman_key: '',
        stream_protocol: 'webrtc',
        driver_mode: 'audio',
        audio_format: 'pcm_s16le_16000_mono',
        window_title: 'ALiver Tencent Digital Human',
      },
    },
    aliyun_avatar: {
      label: '阿里云万相数字人（预留适配）',
      name: 'Aliyun Avatar Dialog',
      apiBaseUrl: '',
      credentials: { dashscope_api_key: '' },
      settings: {
        model: 'avatar-dialog',
        avatar_id: '',
        avatar_code: '',
        stream_protocol: 'aliyun_rtc',
        driver_mode: 'audio',
        audio_format: 'pcm_s16le_16000_mono',
        window_title: 'ALiver Aliyun Avatar',
      },
    },
    baidu_xiling: {
      label: '百度曦灵数字人（预留适配）',
      name: 'Baidu Xiling',
      apiBaseUrl: '',
      credentials: { app_id: '', app_key: '' },
      settings: {
        digital_human_id: '',
        asset_id: '',
        render_mode: 'windows_sdk',
        driver_mode: 'audio',
        audio_format: 'pcm_s16le_16000_mono',
        sdk_path: '',
        window_title: 'ALiver Baidu Xiling',
      },
    },
  };

  Object.entries(catalog).forEach(([value, row]) => {
    if ([...typeSelect.options].some(option => option.value === value)) return;
    const option = document.createElement('option');
    option.value = value;
    option.textContent = row.label;
    typeSelect.appendChild(option);
  });

  const box = document.createElement('div');
  box.id = 'aliver-provider-catalog';
  box.className = 'actions';
  box.innerHTML = `
    <button type="button" class="secondary" data-provider-template="tencent_digital_human">腾讯模板</button>
    <button type="button" class="secondary" data-provider-template="aliyun_avatar">阿里模板</button>
    <button type="button" class="secondary" data-provider-template="baidu_xiling">百度模板</button>
  `;
  submit.before(box);

  box.querySelectorAll('[data-provider-template]').forEach(button => {
    button.addEventListener('click', () => {
      const key = button.dataset.providerTemplate;
      const row = catalog[key];
      typeSelect.value = key;
      form.querySelector('[name="name"]').value = row.name;
      form.querySelector('[name="api_base_url"]').value = row.apiBaseUrl;
      form.querySelector('[name="credentials"]').value = JSON.stringify(row.credentials, null, 2);
      form.querySelector('[name="settings"]').value = JSON.stringify(row.settings, null, 2);
      toast(`${row.label}配置模板已填入。当前为 Provider/Bridge 预留适配层，尚未建立厂商 RTC 媒体连接。`);
    });
  });

  const bridgeSelect = document.getElementById('session-bridge');
  const bridgeLabel = bridgeSelect?.closest('label');
  if (bridgeLabel?.firstChild) {
    bridgeLabel.firstChild.textContent = 'Bridge（实时数字人供应商必选）';
  }
})();
