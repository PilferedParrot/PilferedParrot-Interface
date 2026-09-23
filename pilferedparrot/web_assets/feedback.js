/* Optional local reports. Wiring this panel makes no requests until it is opened. */
(() => {
  const categories = ["usage", "problems", "preferences", "local_changes"];
  const $ = selector => document.querySelector(selector);
  let api, status = null, preview = null, previewRevision = null;
  let generation = 0, busy = false, pendingConsent = null;

  function message(text) { $("#feedbackStatus").textContent = text; }
  function controls() {
    const ready = status?.available === true;
    categories.forEach(category => {
      const input = $(`[data-feedback-consent="${category}"]`);
      input.checked = pendingConsent?.category === category ? pendingConsent.enabled : status?.consent?.[category] === true;
      input.disabled = busy || !ready || (status.disabled && !input.checked);
    });
    $("#feedbackReviewButton").disabled = busy || !ready || status.disabled;
    $("#feedbackClear").disabled = busy || !ready;
    $("#feedbackReset").disabled = busy || !ready;
    $("#feedbackReview").disabled = busy || !preview;
    $("#feedbackDownload").disabled = busy || !ready || status.disabled || !preview || !$("#feedbackReview").checked;
  }
  function invalidate() {
    generation += 1; preview = null; previewRevision = null;
    $("#feedbackPreview").hidden = true;
    $("#feedbackPreview").textContent = "";
    $("#feedbackReview").checked = false;
    controls();
  }
  function applyStatus(value) {
    status = value;
    if (preview && (value.revision !== previewRevision || value.disabled || !value.available)) invalidate();
    message(value.available !== true ? "Feedback storage is unavailable." : value.disabled
      ? "Collection is disabled by the environment. You can still turn choices off and clear counts."
      : "Optional local counts · up to 30 days, removed on the next access after expiry.");
    controls();
  }
  async function action(task) {
    if (busy) return;
    busy = true; controls(); message("");
    try { await task(); }
    catch (error) { message(error.message || "Feedback could not be updated."); }
    finally { busy = false; controls(); }
  }
  function refresh() {
    return action(async () => {
      try { applyStatus(await api("/api/feedback")); }
      catch (error) { status = null; invalidate(); throw error; }
    });
  }
  function saveConsent(input) {
    const category = input.dataset.feedbackConsent, enabled = input.checked;
    pendingConsent = {category, enabled};
    invalidate();
    return action(async () => {
      try {
        applyStatus(await api("/api/feedback/consent", {method: "POST", body: JSON.stringify({
          policy_version: 1, category, enabled,
        })}));
      } finally { pendingConsent = null; }
    });
  }
  function reviewReport() {
    if (status?.available !== true || status.disabled || busy) return;
    invalidate();
    const body = {};
    if ($("#feedbackIncludeWritten").checked) {
      const description = $("#feedbackDescription").value.trim();
      if (!description) { message("Describe what happened, or leave written feedback unchecked."); return; }
      body.feedback = {kind: $("#feedbackKind").value, area: $("#feedbackArea").value,
        scope: $("#feedbackScope").value, description, change: $("#feedbackChange").value.trim()};
    }
    const requested = generation;
    return action(async () => {
      const response = await api("/api/feedback/report", {method: "POST", body: JSON.stringify(body)});
      if (requested !== generation) return;
      preview = response.report; previewRevision = response.revision;
      $("#feedbackPreview").textContent = JSON.stringify(preview, null, 2);
      $("#feedbackPreview").hidden = false;
      message("Review the exact report below. Downloading saves a file; it does not submit it.");
    });
  }
  function downloadReport() {
    if (!preview || !$("#feedbackReview").checked || busy) return;
    const requested = generation, reviewed = preview, revision = previewRevision;
    return action(async () => {
      const latest = await api("/api/feedback");
      if (requested !== generation) return;
      applyStatus(latest);
      if (!latest.available || latest.disabled || latest.revision !== revision) {
        invalidate(); message("Choices or stored counts were cleared; review a fresh report before downloading."); return;
      }
      const url = URL.createObjectURL(new Blob([JSON.stringify(reviewed, null, 2) + "\n"], {type: "application/json"}));
      const link = document.createElement("a"); link.href = url; link.download = "pilferedparrot-feedback.json";
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      message("Report downloaded. Nothing was submitted.");
    });
  }
  function clear(reset) {
    if (busy) return;
    invalidate();
    return action(async () => {
      applyStatus(await api(reset ? "/api/feedback/reset" : "/api/feedback/clear", {method: "POST", body: "{}"}));
    });
  }
  function connect(apiFunction) {
    const details = $("#feedbackDetails");
    if (!details || api) return;
    api = apiFunction; controls();
    details.addEventListener("toggle", () => { if (details.open) refresh(); });
    $("#preferencesDialog").addEventListener("close", () => { details.open = false; invalidate(); });
    details.addEventListener("change", event => {
      if (event.target.matches("[data-feedback-consent]")) saveConsent(event.target);
      else if (event.target.id === "feedbackReview") controls();
      else if (event.target.closest(".feedback-written")) invalidate();
    });
    details.addEventListener("input", event => {
      if (event.target.closest(".feedback-written")) invalidate();
    });
    $("#feedbackReviewButton").addEventListener("click", reviewReport);
    $("#feedbackDownload").addEventListener("click", downloadReport);
    $("#feedbackClear").addEventListener("click", () => clear(false));
    $("#feedbackReset").addEventListener("click", () => clear(true));
  }
  globalThis.PilferedParrotFeedback = {connect};
})();
