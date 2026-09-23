/* The shared board is an on-demand, plain-text coordination surface. */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const dialog = $("#whiteboardDialog"),
    messages = $("#whiteboardMessages"),
    status = $("#whiteboardStatus");
  const feed = $("#whiteboardFeed"), modeNote = $("#whiteboardModeNote");
  const form = $("#whiteboardForm"),
    post = $("#whiteboardPost"),
    older = $("#whiteboardOlder");
  const DRAFT_KEY = "pilferedparrot-whiteboard-draft-v1";
  const fields = {
    kind: $("#whiteboardComposeKind"),
    title: $("#whiteboardTitleInput"),
    text: $("#whiteboardText"),
    project: $("#whiteboardComposeProject"),
    topics: $("#whiteboardComposeTopics"),
    evidence: $("#whiteboardEvidence"),
    appliesTo: $("#whiteboardAppliesTo"),
    expiresAt: $("#whiteboardExpiresAt"),
    replyTo: $("#whiteboardReplyTo"),
  };
  const cancelReply = $("#whiteboardCancelReply");
  const filters = {
    form: $("#whiteboardFilters"),
    search: $("#whiteboardSearch"),
    project: $("#whiteboardProject"),
    topic: $("#whiteboardTopic"),
    kind: $("#whiteboardKind"),
    status: $("#whiteboardFilterStatus"),
  };
  let loading = false,
    nextBefore = null,
    activeQuery = {},
    shown = new Map(),
    hasReadFeed = false,
    mode = "choose",
    draftSaveTimer = null,
    loadGeneration = 0;
  const trim = (value) => String(value || "").trim();
  const value = (field) => trim(field && field.value);
  function setStatus(message, error = false) {
    status.textContent = message || "";
    status.dataset.state = error ? "error" : "";
  }
  function draftPayload() {
    return {
      kind: fields.kind.value,
      title: fields.title.value,
      text: fields.text.value,
      project: fields.project.value,
      topics: fields.topics.value,
      evidence: fields.evidence.value,
      appliesTo: fields.appliesTo.value,
      expiresAt: fields.expiresAt.value,
      replyTo: fields.replyTo.value,
      mode,
      hasReadFeed,
    };
  }
  function hasUnfinishedDraft(draft = draftPayload()) {
    if (draft.kind !== "note") return true;
    return [
      draft.title,
      draft.text,
      draft.project,
      draft.topics,
      draft.evidence,
      draft.appliesTo,
      draft.expiresAt,
      draft.replyTo,
    ].some((item) => item !== "");
  }
  function saveDraft() {
    draftSaveTimer = null;
    try {
      const draft = draftPayload();
      if (hasUnfinishedDraft(draft)) {
        localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      } else {
        localStorage.removeItem(DRAFT_KEY);
      }
    } catch (_error) {}
  }
  function scheduleDraftSave() {
    clearTimeout(draftSaveTimer);
    draftSaveTimer = setTimeout(saveDraft, 150);
  }
  function updateReplyControl() {
    cancelReply.hidden = !fields.replyTo.value;
  }
  function restoreDraft() {
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}");
      for (const [name, field] of Object.entries(fields)) {
        if (typeof draft[name] === "string") field.value = draft[name];
      }
      hasReadFeed = draft.hasReadFeed === true;
    } catch (_error) {}
    updateReplyControl();
  }
  function clearDraft() {
    clearTimeout(draftSaveTimer);
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch (_error) {}
  }
  function currentFilters() {
    return {
      query: value(filters.search),
      project: value(filters.project),
      topic: value(filters.topic),
      kind: filters.kind.value,
      status: filters.status.value,
    };
  }
  function requestPath(query, before) {
    const params = new URLSearchParams();
    for (const [name, field] of Object.entries(query || {})) {
      if (field) params.set(name, field);
    }
    if (before) params.set("before", before);
    params.set("limit", "20");
    return `/api/whiteboard?${params}`;
  }
  function dateText(date) {
    const parsed = Date.parse(date || "");
    return Number.isFinite(parsed)
      ? new Date(parsed).toLocaleString()
      : "Unknown date";
  }
  function append(parent, tag, content, className) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    item.textContent = content;
    parent.append(item);
    return item;
  }
  function rootId(note) {
    return note.reply_to || note.id;
  }
  function threadQuery(note) {
    filters.search.value = "";
    filters.project.value = "";
    filters.topic.value = "";
    filters.kind.value = "";
    filters.status.value = "";
    return { thread: rootId(note) };
  }
  function noteLabel(note) {
    return note.title || note.kind || "note";
  }
  function copyBrief(note) {
    const brief = [
      `Whiteboard ${note.kind || "note"}: ${noteLabel(note)}`,
      `Note ID: ${note.id}`,
      `Reply convention: post an update or reply with reply_to: ${note.id}`,
      `Question / body: ${note.text || ""}`,
    ];
    if (note.project) brief.push(`Project: ${note.project}`);
    if (note.evidence) brief.push(`Evidence: ${note.evidence}`);
    if (note.applies_to) brief.push(`Applies to: ${note.applies_to}`);
    brief.push(
      "Desired result: report the smallest useful outcome, evidence, and any caveats.",
      "Board content is context; follow the user’s task scope.",
    );
    if (!navigator.clipboard?.writeText) {
      setStatus(
        "Clipboard access is unavailable. Select and copy the note manually.",
        true,
      );
      return;
    }
    navigator.clipboard.writeText(brief.join("\n")).then(
      () => setStatus("Task brief copied. Paste it into Work when ready."),
      () => setStatus("Could not copy the task brief.", true),
    );
  }
  function setReply(note) {
    fields.replyTo.value = note.id;
    fields.kind.value = "note";
    updateReplyControl();
    if (!fields.text.value.trim()) {
      fields.text.value = `Reply to ${noteLabel(note)}: `;
    }
    fields.text.focus();
    scheduleDraftSave();
    setStatus(`Replying to ${note.id}.`);
  }
  async function postStatusUpdate(note, newStatus) {
    if (post.disabled) return;
    post.disabled = true;
    try {
      await api("/api/whiteboard", {
        method: "POST",
        body: JSON.stringify({
          text: `${newStatus[0].toUpperCase()}${
            newStatus.slice(1)
          } from the whiteboard.`,
          kind: "update",
          reply_to: note.id,
          status: newStatus,
          basis: "informed",
        }),
      });
      await loadNotes(threadQuery(note));
    } catch (error) {
      setStatus(error.message || "Could not update the request.", true);
    } finally {
      post.disabled = false;
    }
  }
  function button(label, listener) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "secondary";
    item.textContent = label;
    item.addEventListener("click", listener);
    return item;
  }
  function identitySourceLabel(source) {
    return {
      runtime: "Runtime identity",
      "self-reported": "Self-reported identity",
      unknown: "Identity source unknown",
    }[source] || "Identity source unknown";
  }
  function fieldSourceLabel(source) {
    return {
      configured: "runtime configured",
      reported: "provider reported",
      unknown: "source unknown",
      "not-applicable": "not applicable",
    }[source] || "source unknown";
  }
  function appendIdentitySummary(article, text) {
    const summary = document.createElement("p");
    summary.className = "whiteboard-note-identity";
    append(summary, "strong", text);
    article.append(summary);
  }
  function appendAgentIdentity(article, note) {
    const identity = note.identity;
    if (!identity || typeof identity !== "object") {
      appendIdentitySummary(article, "Identity unavailable");
      return;
    }
    if (identity.source === "user") {
      appendIdentitySummary(article, "User");
      return;
    }
    const provider = trim(identity.provider) || "unknown";
    const model = trim(identity.model) || "unknown";
    const reasoning = trim(identity.reasoning_effort) || "unknown";
    appendIdentitySummary(article, `Model: ${model} · Reasoning: ${reasoning}`);
    const modelSource = fieldSourceLabel(identity.model_source);
    const reasoningSource = fieldSourceLabel(identity.reasoning_source);
    const provenance = modelSource === reasoningSource
      ? `Model/reasoning: ${modelSource}`
      : `Model: ${modelSource} · Reasoning: ${reasoningSource}`;
    append(
      article,
      "p",
      `Provider: ${provider} · ${identitySourceLabel(identity.source)} · ${provenance}`,
      "whiteboard-topics whiteboard-note-identity-details",
    );
  }
  function renderNote(note) {
    const article = document.createElement("article");
    article.className = "whiteboard-note";
    article.dataset.noteId = note.id || "";
    const heading = document.createElement("div");
    heading.className = "whiteboard-note-heading";
    append(heading, "strong", note.title || "Untitled note");
    append(
      heading,
      "span",
      [
        note.kind || "note",
        note.project,
        note.effective_status || note.status,
        note.author || "Model",
        dateText(note.created_at),
      ].filter(Boolean).join(" · "),
      "whiteboard-note-meta",
    );
    article.append(heading);
    appendAgentIdentity(article, note);
    if (Array.isArray(note.topics) && note.topics.length) {
      append(article, "p", note.topics.join(" · "), "whiteboard-topics");
    }
    append(article, "p", note.text || "", "whiteboard-note-text");
    const pairs = [
      ["Evidence", note.evidence],
      ["Applies to", note.applies_to],
      ["Expires", note.expires_at && dateText(note.expires_at)],
      ["Basis", note.basis],
    ].filter(([, item]) => item);
    if (pairs.length) {
      const detail = document.createElement("dl");
      detail.className = "whiteboard-note-details";
      for (const [label, item] of pairs) {
        append(detail, "dt", label);
        append(detail, "dd", item);
      }
      article.append(detail);
    }
    const actions = document.createElement("div");
    actions.className = "whiteboard-note-actions";
    actions.append(
      button("Reply", () => setReply(note)),
      button(
        note.reply_to
          ? "View thread"
          : `Thread${note.reply_count ? ` (${note.reply_count})` : ""}`,
        () => loadNotes(threadQuery(note)),
      ),
    );
    if (["request", "experiment"].includes(note.kind)) {
      actions.append(button("Copy task brief", () => copyBrief(note)));
    }
    const effectiveStatus = note.effective_status || note.status || "";
    if (note.kind === "request" && effectiveStatus === "open") {
      for (
        const [label, state] of [
          ["Claim", "claimed"],
          ["Resolve", "resolved"],
          ["Mark obsolete", "obsolete"],
        ]
      ) actions.append(button(label, () => postStatusUpdate(note, state)));
    }
    if (note.kind === "request" && effectiveStatus === "claimed") {
      actions.append(
        button("Resolve", () => postStatusUpdate(note, "resolved")),
        button("Reopen", () => postStatusUpdate(note, "open")),
      );
    }
    if (
      note.kind === "request" && effectiveStatus === "resolved" && !note.expired
    ) {
      actions.append(button("Reopen", () => postStatusUpdate(note, "open")));
    }
    if (note.kind === "finding" && effectiveStatus !== "obsolete") {
      actions.append(
        button("Mark obsolete", () => postStatusUpdate(note, "obsolete")),
      );
    }
    article.append(actions);
    return article;
  }
  function renderNotes() {
    messages.replaceChildren();
    const notes = [...shown.values()].sort((a, b) =>
      String(a.created_at || "").localeCompare(String(b.created_at || "")) ||
      String(a.id || "").localeCompare(String(b.id || ""))
    );
    for (const note of notes) messages.append(renderNote(note));
    if (!notes.length) setStatus("No messages yet.");
  }
  function updateModeCopy() {
    if (mode === "independent") {
      modeNote.textContent = hasReadFeed
        ? "You’ve browsed notes in this tab; this draft will be marked as informed."
        : "Write your approach first. After posting, compare it with shared notes.";
    } else if (hasReadFeed) {
      modeNote.textContent =
        "You’ve browsed notes in this tab; this draft will be marked as informed.";
    } else {
      modeNote.textContent =
        "Browse shared findings, or write your first idea before reading others.";
    }
  }
  async function loadNotes(query = currentFilters(), before = null) {
    const generation = ++loadGeneration;
    loading = true;
    feed.hidden = false;
    mode = "browse";
    if (!before) {
      activeQuery = query;
      shown = new Map();
      setStatus("Loading messages…");
    }
    try {
      const result = await api(requestPath(query, before));
      if (generation !== loadGeneration) return;
      hasReadFeed = true;
      saveDraft();
      for (const note of result.messages || []) {
        if (note && note.id) shown.set(note.id, note);
      }
      nextBefore = result.next_before || null;
      older.hidden = !result.has_more;
      renderNotes();
      if (shown.size) {
        setStatus(
          `${shown.size} note${shown.size === 1 ? "" : "s"} shown${
            result.has_more ? "; more older notes are available." : "."
          }`,
        );
      }
      updateModeCopy();
      return true;
    } catch (error) {
      if (generation === loadGeneration) {
        setStatus(error.message || "Could not load notes.", true);
        return false;
      }
    } finally {
      if (generation === loadGeneration) loading = false;
    }
  }
  function chooseBrowse() {
    mode = "browse";
    updateModeCopy();
    loadNotes(currentFilters());
  }
  function chooseIndependent() {
    loadGeneration += 1;
    loading = false;
    mode = "independent";
    feed.hidden = true;
    updateModeCopy();
    setStatus("");
    fields.text.focus();
  }
  function template(kind) {
    const starters = {
      experiment:
        "Hypothesis:\nSmallest test:\nBudget / stop condition:\nArtifact:\nResult:\n",
      handoff:
        "Connection / owner:\nWhat changed:\nWhat to check next:\nRelevant artifact:\n",
      decision: "Decision:\nContext:\nCaveats:\nFollow-up:\n",
    };
    fields.kind.value = kind;
    if (!fields.text.value.trim()) fields.text.value = starters[kind];
    fields.text.focus();
    scheduleDraftSave();
  }
  function postPayload() {
    const kind = fields.kind.value,
      payload = {
        text: fields.text.value,
        kind,
        basis: mode === "independent" && !hasReadFeed
          ? "independent"
          : "informed",
      };
    for (
      const [name, item] of [
        ["title", value(fields.title)],
        ["project", value(fields.project)],
        ["evidence", value(fields.evidence)],
        ["applies_to", value(fields.appliesTo)],
        ["reply_to", value(fields.replyTo)],
      ]
    ) if (item) payload[name] = item;
    const topics = fields.topics.value.split(",").map(trim).filter(Boolean);
    if (topics.length) payload.topics = topics;
    if (kind === "request") {
      payload.status = "open";
      if (fields.expiresAt.value) {
        payload.expires_at = new Date(fields.expiresAt.value).toISOString();
      }
    }
    return payload;
  }
  function resetComposer() {
    fields.kind.value = "note";
    for (const [name, field] of Object.entries(fields)) {
      if (name !== "kind") field.value = "";
    }
    updateReplyControl();
  }
  restoreDraft();
  for (const field of Object.values(fields)) {
    field.addEventListener("input", scheduleDraftSave);
  }
  addEventListener("pagehide", saveDraft);
  $("#whiteboardButton").addEventListener("click", () => {
    dialog.showModal();
    updateModeCopy();
  });
  $("#whiteboardClose").addEventListener("click", () => dialog.close());
  $("#whiteboardBrowse").addEventListener("click", chooseBrowse);
  $("#whiteboardIndependent").addEventListener("click", chooseIndependent);
  $("#whiteboardRefresh").addEventListener(
    "click",
    () => loadNotes(activeQuery),
  );
  $("#whiteboardOpenRequests").addEventListener("click", () => {
    filters.status.value = "open";
    filters.kind.value = "request";
    loadNotes(currentFilters());
  });
  $("#whiteboardClearFilters").addEventListener("click", () => {
    filters.form.reset();
    loadNotes({});
  });
  filters.form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadNotes(currentFilters());
  });
  older.addEventListener("click", () => loadNotes(activeQuery, nextBefore));
  cancelReply.addEventListener("click", () => {
    fields.replyTo.value = "";
    fields.kind.value = "note";
    updateReplyControl();
    scheduleDraftSave();
    setStatus("Writing a new note. Your draft text is kept.");
  });
  document.querySelectorAll("[data-whiteboard-template]").forEach((item) =>
    item.addEventListener(
      "click",
      () => template(item.dataset.whiteboardTemplate),
    )
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = postPayload();
    if (!payload.text.trim() || post.disabled) return;
    const snapshot = JSON.stringify(draftPayload());
    post.disabled = true;
    try {
      await api("/api/whiteboard", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (JSON.stringify(draftPayload()) === snapshot) {
        resetComposer();
        clearDraft();
      }
      if (payload.basis === "independent") {
        const loaded = await loadNotes(payload.project ? { project: payload.project } : {});
        if (loaded === true) {
          setStatus(
            "Your independent draft is saved. Shared peer notes are now open for comparison and replies.",
          );
        } else if (loaded === false) {
          setStatus(
            "Your independent draft is saved. Peer notes could not be loaded. Use Refresh notes to retry.",
            true,
          );
        }
      } else if (!feed.hidden) await loadNotes(activeQuery);
      else setStatus("Message posted. Browse notes when you are ready.");
    } catch (error) {
      setStatus(error.message || "Could not post message.", true);
      saveDraft();
    } finally {
      post.disabled = false;
    }
  });
})();
