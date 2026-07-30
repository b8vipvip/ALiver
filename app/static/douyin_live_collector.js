(() => {
  let installed = false;
  let lastStatus = null;

  function extensionId() {
    return document.getElementById('auto-director-extension')?.value || '';
  }

  function panel() {
    return document.getElementById('douyin-live-collector-panel');
  }

  function collectorConfig() {
    return {
      aliver_url: location.origin,
      admin_token: localStorage.getItem('aliverAdminToken') || '',
      extension_id: extensionId(),
      collector_id: 'aliver-douyin-live-companion',
      heartbeat_seconds: 5,
      batch_max_items: 100,
      request_timeout_seconds: 8,
    };
  }

  function downloadConfig() {
    const config = collectorConfig();
    if (!config.extension_id) throw new Error('请先选择 Chrome 导演扩展');
    if (!config.admin_token) throw new Error('请先在右上角保存 ALiver 管理令牌');
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'douyin_collector.json';
    anchor.click();
    URL.revokeObjectURL(url);
    toast('采集器配置已生成。请放到采集器 exe 同目录。');
  }

  function renderStatus(value) {
    lastStatus = value;
    const badge = document.getElementById('douyin-collector-badge');
    const diagnosis = document.getElementById('douyin-collector-diagnosis');
    const metrics = document.getElementById('douyin-collector-metrics');
    const recent = document.getElementById('douyin-collector-recent');
    if (!badge || !diagnosis || !metrics || !recent) return;

    badge.textContent = value.connected ? '采集器在线' : '采集器离线';
    badge.className = `badge ${value.connected ? 'good' : 'warn'}`;
    diagnosis.className = `diagnosis ${value.connected ? 'ok' : 'warn'}`;
    diagnosis.textContent = value.connected
      ? `已连接直播伴侣采集器 · 伴侣 ${value.mate_version || '未知版本'} · 最后心跳 ${formatTime(value.last_seen_at)}`
      : '尚未收到采集器心跳。先生成配置，再由直播伴侣互动插件启动采集器。';

    const counts = value.counts || {};
    metrics.innerHTML = `
      <article><span>收到</span><strong>${Number(value.received || 0)}</strong><small>原始消息</small></article>
      <article><span>进入导演</span><strong>${Number(value.accepted || 0)}</strong><small>评论/礼物等</small></article>
      <article><span>重复</span><strong>${Number(value.duplicates || 0)}</strong><small>按 msg_id 去重</small></article>
      <article><span>忽略</span><strong>${Number(value.ignored || 0)}</strong><small>取消关注/未知类型</small></article>
      <article><span>评论</span><strong>${Number(counts.live_comment || 0)}</strong><small>live_comment</small></article>
      <article><span>礼物</span><strong>${Number(counts.live_gift || 0)}</strong><small>live_gift</small></article>
      <article><span>关注</span><strong>${Number(counts.live_follow || 0)}</strong><small>live_follow</small></article>
      <article><span>点赞</span><strong>${Number(counts.live_like || 0)}</strong><small>live_like</small></article>
    `;

    recent.innerHTML = (value.recent || []).slice().reverse().map(item => `
      <div class="douyin-event-row">
        <div><strong>${escapeHtml(item.event_type || item.type || 'unknown')}</strong> · ${escapeHtml(item.user_name || '')}</div>
        <div>${escapeHtml(item.content || item.reason || '')}</div>
        <small>${escapeHtml(item.status || '')} ${item.score !== undefined ? `· ${item.score}分` : ''} · ${formatTime(item.at)}</small>
      </div>
    `).join('') || '<p class="hint">尚未收到真实互动消息。</p>';
  }

  async function refreshStatus() {
    const id = extensionId();
    if (!id || !panel()) return;
    try {
      renderStatus(await api(`/api/douyin-live/status?extension_id=${encodeURIComponent(id)}`));
    } catch (error) {
      const diagnosis = document.getElementById('douyin-collector-diagnosis');
      if (diagnosis) {
        diagnosis.className = 'diagnosis bad';
        diagnosis.textContent = error.message;
      }
    }
  }

  async function simulate() {
    const id = extensionId();
    if (!id) throw new Error('请先选择 Chrome 导演扩展');
    const seed = Date.now();
    const result = await api('/api/douyin-live/simulate', {
      method: 'POST',
      body: JSON.stringify({
        extension_id: id,
        collector_id: 'aliver-ui-simulator',
        event_name: 'OPEN_LIVE_DATA',
        metadata: { simulated: true, plugin_version: 'ui' },
        payload: [
          { msg_id: `comment-${seed}`, timestamp: seed, msg_type: 2, msg_type_str: 'live_comment', nickname: '测试观众', content: '数字人直播是怎么实现的？' },
          { msg_id: `gift-${seed}`, timestamp: seed + 1, msg_type: 3, msg_type_str: 'live_gift', nickname: '礼物观众', gift_name: '小心心', gift_num: 1, sec_gift_id: 'debug' },
          { msg_id: `follow-${seed}`, timestamp: seed + 2, msg_type: 5, msg_type_str: 'live_follow', nickname: '新关注观众', user_follow_action: 1 },
        ],
      }),
    });
    toast(`已模拟 ${result.accepted} 条抖音互动事件`);
    await refreshStatus();
    if (typeof loadAutoDirector === 'function') await loadAutoDirector();
  }

  function install() {
    if (installed) return true;
    const tab = document.getElementById('tab-auto-director');
    const eventForm = document.getElementById('auto-director-event-form');
    if (!tab || !eventForm) return false;
    installed = true;

    const article = document.createElement('article');
    article.id = 'douyin-live-collector-panel';
    article.className = 'panel douyin-live-collector-panel';
    article.innerHTML = `
      <div class="section-title">
        <div>
          <h2>真实抖音互动采集器</h2>
          <p class="hint">直播伴侣官方互动插件 → OPEN_LIVE_DATA → ALiver → 专业总导演。支持评论、礼物、点赞、关注和粉丝团；分享仅保留兼容入口。</p>
        </div>
        <span id="douyin-collector-badge" class="badge warn">采集器离线</span>
      </div>
      <div class="actions">
        <button id="douyin-collector-config" type="button">生成采集器配置</button>
        <button id="douyin-collector-simulate" type="button" class="secondary">模拟评论/礼物/关注</button>
        <button id="douyin-collector-refresh" type="button" class="secondary">刷新状态</button>
      </div>
      <div id="douyin-collector-diagnosis" class="diagnosis warn">等待采集器连接。</div>
      <div id="douyin-collector-metrics" class="douyin-collector-metrics"></div>
      <details>
        <summary>最近收到的互动</summary>
        <div id="douyin-collector-recent" class="douyin-collector-recent"><p class="hint">尚未收到真实互动消息。</p></div>
      </details>
      <details>
        <summary>官方能力与接入限制</summary>
        <div class="douyin-collector-notes">
          <p>需要在抖音开放平台创建或获批直播互动插件，并申请评论、礼物、点赞、关注等互动数据权限。</p>
          <p>直播伴侣官方 OPEN_LIVE_DATA 当前文档列出 live_like、live_comment、live_gift、live_fansclub、live_follow；没有独立 live_share 消息。</p>
          <p>本面板不会抓取网页私有接口，也不会绕过平台权限。</p>
        </div>
      </details>
    `;
    const eventPanel = eventForm.closest('.panel');
    eventPanel?.insertAdjacentElement('beforebegin', article);

    document.getElementById('douyin-collector-config').addEventListener('click', () => {
      try { downloadConfig(); } catch (error) { toast(error.message, true); }
    });
    document.getElementById('douyin-collector-simulate').addEventListener('click', () => {
      simulate().catch(error => toast(error.message, true));
    });
    document.getElementById('douyin-collector-refresh').addEventListener('click', () => {
      refreshStatus().catch(error => toast(error.message, true));
    });
    document.getElementById('auto-director-extension')?.addEventListener('change', () => {
      setTimeout(() => refreshStatus().catch(() => {}), 100);
    });
    refreshStatus().catch(() => {});
    return true;
  }

  if (!install()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (install() || attempts > 100) clearInterval(timer);
    }, 200);
  }

  setInterval(() => {
    if (document.getElementById('tab-auto-director')?.classList.contains('active')) {
      refreshStatus().catch(() => {});
    }
  }, 3000);
})();
