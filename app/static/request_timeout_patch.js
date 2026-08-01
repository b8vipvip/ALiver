(() => {
  const DEFAULT_TIMEOUT_MS = 20000;

  function requestHeaders(json = false) {
    const value = {};
    const token = localStorage.getItem('aliverAdminToken');
    if (token) value['X-ALiver-Token'] = token;
    if (json) value['Content-Type'] = 'application/json';
    return value;
  }

  async function boundedApi(path, options = {}) {
    const timeoutMs = Math.max(1000, Number(options.timeoutMs || DEFAULT_TIMEOUT_MS));
    const controller = new AbortController();
    const callerSignal = options.signal;
    if (callerSignal) {
      if (callerSignal.aborted) controller.abort();
      else callerSignal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    const fetchOptions = { ...options };
    delete fetchOptions.timeoutMs;
    fetchOptions.signal = controller.signal;
    fetchOptions.headers = {
      ...requestHeaders(Boolean(fetchOptions.body)),
      ...(fetchOptions.headers || {}),
    };

    try {
      const response = await fetch(path, fetchOptions);
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try {
          const body = await response.json();
          detail = body.detail || JSON.stringify(body);
        } catch (_) {}
        throw new Error(detail);
      }
      if (response.status === 204) return null;
      return response.json();
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error(`请求超过 ${Math.ceil(timeoutMs / 1000)} 秒未完成：${path}`);
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  async function boundedBridgeCommand(id, commandType, payload = {}, timeoutSeconds = 30) {
    const commandTimeout = Math.max(1, Number(timeoutSeconds || 30));
    const value = await boundedApi(`/api/bridges/${id}/commands`, {
      method: 'POST',
      timeoutMs: (commandTimeout + 8) * 1000,
      body: JSON.stringify({
        command_type: commandType,
        payload,
        timeout_seconds: commandTimeout,
      }),
    });
    if (value && value.type === 'result') {
      if (!value.ok) throw new Error(value.error || `${commandType} failed`);
      return value.data || {};
    }
    return value && value.data !== undefined ? value.data : value;
  }

  function revealConsole() {
    document.documentElement.classList.remove('aliver-shell-booting');
    document.body.classList.add('aliver-shell-ready');
  }

  function waitForShell(startedAt = performance.now()) {
    if (document.getElementById('aliver-app-shell')) {
      revealConsole();
      return;
    }
    if (performance.now() - startedAt >= 5000) {
      revealConsole();
      document.body.classList.add('aliver-shell-fallback');
      return;
    }
    window.setTimeout(() => waitForShell(startedAt), 40);
  }

  window.api = boundedApi;
  window.sendBridgeCommand = boundedBridgeCommand;
  waitForShell();
})();
