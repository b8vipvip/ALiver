(() => {
  function parseBackendTimestamp(value) {
    if (value == null || value === '') return null;
    let text = String(value).trim();
    if (!text) return null;

    // SQLite commonly returns UTC datetimes without a timezone suffix. A bare
    // ISO timestamp is interpreted by browsers as local time, which made an
    // actual 15:36 event appear as 07:36 on UTC+8 systems. Treat backend-naive
    // ISO values as UTC while preserving timestamps that already include Z or
    // an explicit offset.
    const naiveIso = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;
    const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
    if (naiveIso.test(text) && !hasZone) {
      text = `${text.replace(' ', 'T')}Z`;
    }

    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  const localFormatTime = value => {
    if (!value) return '无';
    const date = parseBackendTimestamp(value);
    return date ? date.toLocaleString() : String(value);
  };

  // app.js declares formatTime in the classic global scope. Replacing both the
  // global property and binding keeps all existing panels compatible.
  window.formatTime = localFormatTime;
  try {
    formatTime = localFormatTime; // eslint-disable-line no-global-assign
  } catch (_) {}

  // refreshAll() starts before this patch is loaded. Refresh the log panel once
  // so even a very fast first response is immediately shown in local time.
  if (typeof window.loadLogs === 'function') {
    window.loadLogs().catch(() => {});
  }
})();
