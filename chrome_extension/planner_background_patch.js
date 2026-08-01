(() => {
  const baseExecuteCommand = executeCommand;

  async function ensurePlannerContent(tabId) {
    try {
      const probe = await chrome.tabs.sendMessage(tabId, { type: 'aliver.plan.probe' });
      if (probe?.ok) return;
    } catch (_) {}
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['planner_content.js'],
    });
    await sleep(100);
  }

  executeCommand = async function executePlannerAwareCommand(message) {
    if (message.command_type !== 'plan_generate') {
      return baseExecuteCommand(message);
    }

    const prior = await cachedResult(message.command_id);
    if (prior) return prior;
    const tab = await resolveCommandTarget();
    await ensureContentScript(tab);
    await ensurePlannerContent(tab.id);

    const response = await sendToChatGpt(tab, {
      type: 'aliver.plan.generate',
      commandId: message.command_id,
      payload: message.payload || {},
    });
    if (!response) throw new Error('ChatGPT planner controller did not return a result.');
    const result = {
      ok: Boolean(response.ok),
      data: {
        ...(response.data || {}),
        target_tab_id: tab.id,
        target_window_id: tab.windowId,
        target_conversation_key: conversationKey(tab.url || ''),
      },
      error: response.error || null,
    };
    await rememberResult(message.command_id, result);
    return result;
  };
})();
