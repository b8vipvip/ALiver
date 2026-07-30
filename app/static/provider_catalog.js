(() => {
  const form = document.getElementById('provider-form');
  const typeSelect = form?.querySelector('[name="provider_type"]');
  const submit = form?.querySelector('button[type="submit"]');
  if (!form || !typeSelect || !submit || document.getElementById('aliver-provider-catalog')) return;

  const catalog = {
    vtube_studio: {
      label: 'VTube Studio（本地虚拟形象）',
      name: 'Local VTube Studio',
      apiBaseUrl: '',
      credentials: {},
      settings: {
        ws_url: 'ws://127.0.0.1:8001',
        plugin_name: 'ALiver',
        plugin_developer: 'b8vipvip',
        require_model_loaded: true,
        auto_reconnect: true,
        reconnect_interval_seconds: 2,
        connect_timeout_seconds: 12,
        authorization_timeout_seconds: 120,
        action_cooldown_ms: 1200,
        audio_device_name: 'CABLE Output (VB-Audio Virtual Cable)',
        mouth_input_parameter: 'VoiceVolume',
        mouth_output_parameter: 'ParamMouthOpenY',
        hotkeys: {
          idle: '',
          talking: '',
          thinking: '',
          wave: '',
          happy: '',
          surprised: '',
          reset: '',
        },
        motion_engine: {
          enabled: false,
          preset: 'gentle',
          fps: 15,
          auto_speech: true,
          voice_parameter: 'VoiceVolume',
          speech_threshold: 0.08,
          speech_hold_ms: 500,
          idle_intensity: 0.5,
          talking_intensity: 1.05,
          action_intensity: 1.0,
          expressions_enabled: true,
          expression_map: {
            thinking: '',
            happy: '',
            surprised: '',
          },
        },
      },
    },
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
    <button type="button" class="secondary" data-provider-template="vtube_studio">VTube Studio 模板</button>
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
      if (key === 'vtube_studio') {
        toast('已填入 VTube Studio 本地模板。启动会话后可在数字人调试页一键启用自然动作。');
      } else {
        toast(`${row.label}配置模板已填入。当前为 Provider/Bridge 预留适配层，尚未建立厂商 RTC 媒体连接。`);
      }
    });
  });

  const bridgeSelect = document.getElementById('session-bridge');
  const bridgeLabel = bridgeSelect?.closest('label');
  if (bridgeLabel?.firstChild) {
    bridgeLabel.firstChild.textContent = 'Bridge（实时/本地数字人供应商必选）';
  }

  const assets = [
    ['link', 'aliver-avatar-debug-style', '/static/avatar_debug_v2.css?v=0.9.9'],
    ['link', 'aliver-avatar-action-runtime-style', '/static/avatar_action_runtime.css?v=0.9.9'],
    ['link', 'aliver-director-plan-generator-style', '/static/director_plan_generator.css?v=0.10.1'],
    ['link', 'aliver-douyin-live-collector-style', '/static/douyin_live_collector.css?v=0.12.0'],
    ['script', 'aliver-avatar-debug-v2', '/static/avatar_debug_v2.js?v=0.9.9'],
    ['script', 'aliver-management-v2', '/static/management_v2.js?v=0.9.9'],
    ['script', 'aliver-audio-route-autostart', '/static/audio_route_autostart.js?v=0.9.9'],
    ['script', 'aliver-vtube-motion-wizard', '/static/vtube_motion_wizard.js?v=0.9.9'],
    ['script', 'aliver-vtube-motion-controls-fix', '/static/vtube_motion_controls_fix.js?v=0.9.9'],
    ['script', 'aliver-avatar-action-runtime', '/static/avatar_action_runtime.js?v=0.9.9'],
    ['script', 'aliver-director-plan-generator', '/static/director_plan_generator.js?v=0.10.1'],
    ['script', 'aliver-douyin-live-collector', '/static/douyin_live_collector.js?v=0.12.0'],
  ];

  assets.forEach(([kind, id, href]) => {
    if (document.getElementById(id)) return;
    if (kind === 'link') {
      const element = document.createElement('link');
      element.id = id;
      element.rel = 'stylesheet';
      element.href = href;
      document.head.appendChild(element);
      return;
    }
    const element = document.createElement('script');
    element.id = id;
    element.src = href;
    element.async = false;
    document.head.appendChild(element);
  });
})();
