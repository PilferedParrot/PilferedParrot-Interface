/* Shared provider allowance presentation for Work and Chat. */
(function () {
  const escapeHtml = globalThis.PilferedParrotMarkdown.escapeHtml;
  const STALE_AFTER_SECONDS = 5 * 60;
  const validNumber = (value) => typeof value === "number" && Number.isFinite(value);

  function windows(budget) {
    const values = Array.isArray(budget?.windows) && budget.windows.length
      ? budget.windows : (budget?.window ? [budget.window] : []);
    return values.filter((item) => validNumber(item?.used_percent));
  }

  function freshness(budget, now = Date.now()) {
    const observed = budget?.observed_at;
    if (!validNumber(observed) || observed <= 0) return { stale: true, label: "Update time unavailable" };
    const age = Math.max(0, now / 1000 - observed);
    if (budget?.refresh_failed) return { stale: true, label: "Refresh failed · last reported usage" };
    if (age > STALE_AFTER_SECONDS) return { stale: true, label: "Usage data is stale" };
    if (age < 60) return { stale: false, label: "Updated just now" };
    return { stale: false, label: `Updated ${Math.floor(age / 60)}m ago` };
  }

  function supportedWindows(provider, budget) {
    if (provider !== "codex" || budget?.usage_status !== "available") return [];
    return windows(budget);
  }

  function resetTime(timestamp) {
    const milliseconds = Number(timestamp) * 1000;
    const date = new Date(milliseconds);
    if (!(milliseconds > 0) || Number.isNaN(date.getTime())) {
      return { short: "Reset unavailable", exact: "Reset time unavailable", datetime: "" };
    }
    const exact = `Resets ${date.toLocaleString([], {
      weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    })}`;
    const minutes = Math.ceil((milliseconds - Date.now()) / 60000);
    if (minutes <= 0) return { short: "Reset due · awaiting update", exact, datetime: date.toISOString() };
    if (minutes < 60) return { short: `Resets in ${minutes}m`, exact, datetime: date.toISOString() };
    if (minutes < 1440) {
      const hours = Math.floor(minutes / 60);
      return { short: `Resets in ${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`, exact, datetime: date.toISOString() };
    }
    return { short: `Resets ${date.toLocaleString([], { weekday: "short", hour: "numeric" })}`, exact, datetime: date.toISOString() };
  }

  function allowanceMarkup(window, stale = false) {
    const used = Math.max(0, Math.min(100, Number(window.used_percent) || 0));
    const remaining = 100 - used;
    const label = window.label || "Included usage";
    const reset = resetTime(window.resets_at);
    const expired = validNumber(window.resets_at) && window.resets_at > 0
      && window.resets_at <= Date.now() / 1000;
    const qualifier = stale || expired ? "Last reported: " : "";
    const resetMarkup = reset.datetime
      ? `<time class="allowance-reset" datetime="${escapeHtml(reset.datetime)}" title="${escapeHtml(reset.exact)}" aria-label="${escapeHtml(reset.exact)}">${escapeHtml(reset.short)}</time>`
      : `<span class="allowance-reset" title="${escapeHtml(reset.exact)}">${escapeHtml(reset.short)}</span>`;
    return `<div class="allowance-row">
      <div class="allowance-head"><span class="allowance-label">${escapeHtml(label)}</span><strong>${qualifier}${Math.round(used)}% used · ${Math.round(remaining)}% left</strong></div>
      <div class="allowance-meter"><div class="allowance-track" role="progressbar" aria-label="${escapeHtml(label)} used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(used)}"><span style="width:${used}%"></span></div>${resetMarkup}</div>
    </div>`;
  }

  function markup(provider, budget) {
    if (provider !== "codex" && !budget?.usage_note) return "";
    const state = freshness(budget);
    const status = budget?.usage_status;
    const items = supportedWindows(provider, budget);
    const unavailable = status !== "available" || !items.length;
    const note = budget?.usage_note || "Allowance unavailable";
    const freshnessLabel = state.label;
    if (unavailable) return `<div class="provider-usage unavailable"><p class="usage-unavailable-note">${escapeHtml(note || "Allowance unavailable")}</p><small>${escapeHtml(freshnessLabel)}</small></div>`;
    return `<div class="provider-usage"><div class="provider-usage-status">${escapeHtml(freshnessLabel)}</div><div class="allowances">${items.map(item => allowanceMarkup(item, state.stale)).join("")}</div></div>`;
  }

  globalThis.PilferedParrotUsage = { windows, freshness, supportedWindows, allowanceMarkup, markup };
}());
