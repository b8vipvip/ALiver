(() => {
  const DEFAULT_RUNDOWN = [
    '开场与暖场|3|完成问好并邀请观众互动|wave|欢迎观众，介绍直播主题，并抛出一个简单问题',
    '互动升温|8|优先回应高质量评论和新关注|happy|挑选容易引发讨论的评论进行回应',
    '主题聊天|18|围绕本场主题连续聊天并穿插互动|thinking|承接最近话题展开，结尾给观众明确接话点',
    '二次拉活|8|互动下降时切换轻松问题|wave|发起所有人都容易回答的快速问题',
    '自然收尾|3|感谢观众并完整结束直播|happy|回顾本场亮点，感谢陪伴并温和告别',
  ].join('\n');

  let installed = false;
  let lastPlan = null;

  function esc(value) {
    if (typeof escapeHtml === 'function') return escapeHtml(value);
    return String(value || '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function rundownToText(rundown) {
    return (Array.isArray(rundown) ? rundown : []).map(item => [
      item.name || '未命名环节',
      Math.max(0.5, Number(item.duration_seconds || 300) / 60),
      item.objective || '',
      item.avatar_action || 'thinking',
      item.cue || '',
    ].join('|')).join('\n');
  }

  function currentSettings(form) {
    const formData = new FormData(form);
    const list = name => String(formData.get(name) || '')
      .split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    return {
      director_name: formData.get('director_name') || '',
      show_title: formData.get('show_title') || '',
      show_goal: formData.get('show_goal') || '',
      host_persona: formData.get('host_persona') || '',
      audience_profile: formData.get('audience_profile') || '',
      director_style: formData.get('director_style') || '',
      opening_script: formData.get('opening_script') || '',
      closing_script: formData.get('closing_script') || '',
      min_score: Number(formData.get('min_score') || 35),
      cooldown_seconds: Number(formData.get('cooldown_seconds') || 12),
      idle_seconds: Number(formData.get('idle_seconds') || 120),
      max_response_seconds: Number(formData.get('max_response_seconds') || 25),
      dedupe_window_seconds: Number(formData.get('dedupe_window_seconds') || 90),
      per_user_cooldown_seconds: Number(formData.get('per_user_cooldown_seconds') || 120),
      max_consecutive_replies: Number(formData.get('max_consecutive_replies') || 4),
      segment_cue_interval_seconds: Number(formData.get('segment_cue_interval_seconds') || 90),
      max_queue_age_seconds: Number(formData.get('max_queue_age_seconds') || 180),
      blocked_keywords: list('blocked_keywords'),
      idle_topics: list('idle_topics'),
    };
  }

  function assign(form, name, value) {
    const field = form.elements[name];
    if (!field || value === null || value === undefined) return;
    field.value = Array.isArray(value) ? value.join('\n') : String(value);
  }

  function applyPlan(form, plan) {
    const textFields = [
      'director_name', 'show_title', 'show_goal', 'host_persona', 'audience_profile',
      'director_style', 'opening_script', 'closing_script',
    ];
    const numberFields = [
      'min_score', 'cooldown_seconds', 'idle_seconds', 'max_response_seconds',
      'dedupe_window_seconds', 'per_user_cooldown_seconds', 'max_consecutive_replies',
      'segment_cue_interval_seconds', 'max_queue_age_seconds',
    ];
    textFields.forEach(name => assign(form, name, plan[name]));
    numberFields.forEach(name => assign(form, name, plan[name]));
    assign(form, 'blocked_keywords', plan.blocked_keywords || []);
    assign(form, 'idle_topics', plan.idle_topics || []);
    assign(form, 'rundown_lines', rundownToText(plan.rundown));
    form.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function renderPreview(result) {
    const preview = document.getElementById('director-plan-preview');
    const summary = result.summary || {};
    const plan = result.plan || {};
    const segments = Array.isArray(plan.rundown) ? plan.rundown : [];
    preview.innerHTML = `
      <div class="director-plan-summary">
        <article><span>直播标题</span><strong>${esc(summary.show_title || plan.show_title)}</strong></article>
        <article><span>预计时长</span><strong>${esc(summary.duration_minutes || 0)} 分钟</strong></article>
        <article><span>节目环节</span><strong>${esc(summary.segment_count || segments.length)} 个</strong></article>
        <article><span>生成方式</span><strong>${result.source === 'ai' ? 'AI 策划' : '本地专业模板'}</strong></article>
      </div>
      <div class="director-plan-segments">
        ${segments.map((item, index) => `
          <div>
            <b>${index + 1}. ${esc(item.name)}</b>
            <span>${(Number(item.duration_seconds || 0) / 60).toFixed(1)} 分钟 · ${esc(item.avatar_action)}</span>
            <small>${esc(item.objective)}</small>
          </div>
        `).join('')}
      </div>
    `;
  }

  async function generatePlan(preferAi) {
    const form = document.getElementById('auto-director-config-form');
    const extensionId = document.getElementById('auto-director-extension')?.value || '';
    const brief = document.getElementById('director-plan-brief')?.value.trim() || '';
    if (!extensionId) throw new Error('请先选择 Chrome 导演扩展');
    if (brief.length < 2) throw new Error('请先用一句话说明直播主题、目标和希望的风格');

    const button = document.getElementById(preferAi ? 'director-plan-generate-ai' : 'director-plan-generate-local');
    const diagnosis = document.getElementById('director-plan-diagnosis');
    const initial = button.textContent;
    button.disabled = true;
    button.textContent = preferAi ? 'AI 正在策划…' : '正在生成模板…';
    diagnosis.className = 'diagnosis warn';
    diagnosis.textContent = preferAi
      ? '正在让 AI 生成导演简报、节目单和节奏参数，请稍候。'
      : '正在生成本地专业直播方案。';
    try {
      const result = await api('/api/auto-director/plan/generate', {
        method: 'POST',
        body: JSON.stringify({
          extension_id: extensionId,
          brief,
          duration_minutes: Number(document.getElementById('director-plan-duration')?.value || 45),
          category: document.getElementById('director-plan-category')?.value || 'chat',
          tone: document.getElementById('director-plan-tone')?.value || 'natural',
          prefer_ai: preferAi,
          api_base_url: form.elements.api_base_url?.value || null,
          model_name: form.elements.model_name?.value || null,
          api_key: form.elements.api_key?.value || null,
          current_settings: currentSettings(form),
        }),
      });
      lastPlan = result.plan;
      applyPlan(form, result.plan);
      renderPreview(result);
      diagnosis.className = `diagnosis ${result.source === 'ai' ? 'ok' : 'warn'}`;
      diagnosis.textContent = result.fallback_reason
        || 'AI 已生成完整直播方案并填入表单。请检查后点击“保存自动导演配置”。';
      toast(result.source === 'ai' ? 'AI 直播方案已生成并填入' : '本地专业方案已生成并填入');
    } finally {
      button.disabled = false;
      button.textContent = initial;
    }
  }

  function install() {
    if (installed) return true;
    const fields = document.getElementById('professional-director-fields');
    const form = document.getElementById('auto-director-config-form');
    if (!fields || !form) return false;
    installed = true;

    const wizard = document.createElement('section');
    wizard.id = 'director-plan-wizard';
    wizard.className = 'director-plan-wizard';
    wizard.innerHTML = `
      <div class="section-title">
        <div>
          <h3>AI 直播方案生成器</h3>
          <p class="hint">只需描述这场直播要做什么，AI 会生成导演简报、主播人设、节目单、动作和节奏参数。生成结果只填入表单，保存后才生效。</p>
        </div>
        <span class="badge">策划助手</span>
      </div>
      <label>一句话直播需求
        <textarea id="director-plan-brief" rows="4" placeholder="例如：做一场45分钟的轻松聊天直播，主题是AI如何改变普通人的生活，多回应观众问题，主播自然甜美，不要像念稿。"></textarea>
      </label>
      <div class="director-plan-options">
        <label>直播类型
          <select id="director-plan-category">
            <option value="chat">轻松聊天</option>
            <option value="ai">AI 科技</option>
            <option value="knowledge">知识分享</option>
            <option value="story">故事陪伴</option>
            <option value="entertainment">娱乐互动</option>
            <option value="custom">自定义主题</option>
          </select>
        </label>
        <label>预计时长（分钟）<input id="director-plan-duration" type="number" min="10" max="240" value="45"></label>
        <label>主播氛围
          <select id="director-plan-tone">
            <option value="natural">自然亲切</option>
            <option value="energetic">活泼明显</option>
            <option value="calm">温和松弛</option>
            <option value="professional">专业清晰</option>
            <option value="humorous">轻松幽默</option>
          </select>
        </label>
      </div>
      <div class="actions">
        <button id="director-plan-generate-ai" type="button">AI 生成完整方案</button>
        <button id="director-plan-generate-local" type="button" class="secondary">生成本地专业模板</button>
      </div>
      <div id="director-plan-diagnosis" class="diagnosis warn">填写直播需求后点击生成。AI 接口未配置或调用失败时会自动回退到本地专业模板。</div>
      <details class="director-plan-preview-box" open>
        <summary>生成方案预览</summary>
        <div id="director-plan-preview"><p class="hint">尚未生成方案。</p></div>
      </details>
    `;
    fields.prepend(wizard);

    document.getElementById('director-plan-generate-ai').addEventListener('click', () => {
      generatePlan(true).catch(error => {
        document.getElementById('director-plan-diagnosis').className = 'diagnosis bad';
        document.getElementById('director-plan-diagnosis').textContent = error.message;
        toast(error.message, true);
      });
    });
    document.getElementById('director-plan-generate-local').addEventListener('click', () => {
      generatePlan(false).catch(error => {
        document.getElementById('director-plan-diagnosis').className = 'diagnosis bad';
        document.getElementById('director-plan-diagnosis').textContent = error.message;
        toast(error.message, true);
      });
    });

    form.addEventListener('submit', () => {
      const rundown = form.elements.rundown_lines;
      if (rundown && !String(rundown.value || '').trim()) {
        rundown.value = lastPlan ? rundownToText(lastPlan.rundown) : DEFAULT_RUNDOWN;
        form.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }, true);
    return true;
  }

  if (!install()) {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (install() || attempts > 100) clearInterval(timer);
    }, 200);
  }
})();
