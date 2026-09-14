/* Server-backed synchronization for the appearance controller. */
(function () {
  const defaults = { tone: "darker", surface: "minimal", readability: "standard" };
  const valid = {
    tone: new Set(["original", "darker"]),
    surface: new Set(["minimal", "balanced", "maximal"]),
    readability: new Set(["standard", "stronger"]),
  };

  function normalize(value) {
    return Object.fromEntries(Object.keys(defaults).map((key) => [
      key, valid[key].has(value?.[key]) ? value[key] : defaults[key],
    ]));
  }

  function connect(api, initialAppearance, reportError = () => {}) {
    if (globalThis.__pilferedParrotAppearanceSync) return globalThis.__pilferedParrotAppearanceSync;

    const controller = globalThis.PilferedParrotAppearance;
    if (!controller || typeof controller.apply !== "function" || typeof controller.current !== "function") {
      return null;
    }

    let current = normalize(initialAppearance || controller.current());
    let revision = 0;
    const revisions = Object.fromEntries(Object.keys(defaults).map(key => [key, 0]));
    const pending = new Map();
    let sending = false;
    let stopped = false;
    let timer = null;
    let refreshChain = Promise.resolve();

    // State returned before a local edit must never roll that edit back. A
    // request records the last revision it could possibly know about.
    function applyServer(value, knownRevisions) {
      const received = normalize(value);
      const next = { ...current };
      Object.keys(defaults).forEach((key) => {
        if (revisions[key] <= knownRevisions[key]) next[key] = received[key];
      });
      current = next;
      controller.apply(current);
    }

    function snapshotRevisions() {
      return { ...revisions };
    }

    function refresh(knownRevisions = snapshotRevisions(), quiet = false, force = false) {
      if (!force && (sending || pending.size)) return Promise.resolve();
      // GETs are serialized so an older focus/poll response cannot arrive
      // after a newer one. Check again when this queued refresh begins: a
      // user may have started a save while an earlier GET was settling.
      const run = async () => {
        if (!force && (sending || pending.size)) return;
        try {
          const received = await api("/api/preferences/appearance");
          applyServer(received, knownRevisions);
        } catch (error) {
          if (!quiet) reportError(error?.message || "Could not refresh appearance preferences.");
        }
      };
      const request = refreshChain.then(run, run);
      refreshChain = request.catch(() => {});
      return request;
    }

    async function sendPending() {
      if (sending) return;
      sending = true;
      while (pending.size) {
        const updates = Object.fromEntries(pending.entries());
        pending.clear();
        const knownRevisions = snapshotRevisions();
        try {
          const received = await api("/api/preferences/appearance", {
            method: "POST", body: JSON.stringify(updates),
          });
          applyServer(received, knownRevisions);
        } catch (error) {
          reportError(error?.message || "Could not save appearance preferences.");
          // A failed write has no retry loop. Fetch once to restore the
          // persisted authority, while preserving a choice made meanwhile.
          await refresh(knownRevisions, true, true);
        }
      }
      sending = false;
    }

    function changed(event) {
      const { key, value } = event.detail || {};
      if (!valid[key]?.has(value)) return;
      revision += 1;
      revisions[key] = revision;
      current = { ...current, [key]: value };
      pending.set(key, value);
      void sendPending();
    }

    function schedulePoll() {
      if (stopped) return;
      timer = globalThis.setTimeout(async () => {
        if (!document.hidden) await refresh(snapshotRevisions(), true);
        schedulePoll();
      }, 1000);
    }

    function refreshWhenVisible() {
      if (!document.hidden) void refresh();
    }

    controller.apply(current);
    document.addEventListener("ppi-appearance-change", changed);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    globalThis.addEventListener("focus", refreshWhenVisible);
    schedulePoll();
    void refresh();

    const connection = {
      disconnect() {
        stopped = true;
        if (timer !== null) globalThis.clearTimeout(timer);
        document.removeEventListener("ppi-appearance-change", changed);
        document.removeEventListener("visibilitychange", refreshWhenVisible);
        globalThis.removeEventListener("focus", refreshWhenVisible);
      },
      refresh: () => refresh(),
    };
    globalThis.__pilferedParrotAppearanceSync = connection;
    return connection;
  }

  globalThis.PilferedParrotAppearanceSync = { connect };
})();
