/* Shared Work and Chat appearance preferences. These are intentionally local:
   appearance is a client preference and does not belong in provider state. */
(function () {
  const KEY = "pilferedparrot.appearance";
  const defaults = { tone: "original", surface: "balanced", readability: "standard" };
  const valid = {
    tone: new Set(["original", "darker"]),
    surface: new Set(["minimal", "balanced", "maximal"]),
    readability: new Set(["standard", "stronger"]),
  };

  function read() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      return Object.fromEntries(Object.keys(defaults).map((key) => [
        key, valid[key].has(saved?.[key]) ? saved[key] : defaults[key],
      ]));
    } catch (_) { return { ...defaults }; }
  }

  const channels = (color) => color.slice(1).match(/../g).map(value => parseInt(value, 16));
  const hex = (rgb) => `#${rgb.map(value => Math.round(value).toString(16).padStart(2, "0")).join("")}`;
  const luminance = (color) => channels(color).map(value => value / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  const mix = (from, to, amount) => hex(channels(from).map((value, index) =>
    value + (channels(to)[index] - value) * amount));
  function readable(background, requested, target) {
    const light = luminance(background);
    const extreme = light > .179 ? "#000000" : "#ffffff";
    const ratio = text => (Math.max(light, luminance(text)) + .05) / (Math.min(light, luminance(text)) + .05);
    for (let step = 0; step <= 100; step++) {
      const candidate = mix(requested, extreme, step / 100);
      if (ratio(candidate) >= target) return candidate;
    }
    return extreme;
  }
  function palette(preferences) {
    const body = document.body;
    const root = getComputedStyle(document.documentElement);
    const themed = body.classList.contains("chrome-theme");
    const get = (name, fallback) => {
      const value = root.getPropertyValue(`--chrome-theme-${name}`).trim();
      return themed && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
    };
    const darken = value => {
      if (preferences.tone !== "darker") return value;
      // Keep dark palettes nuanced; pull bright palettes into a dark luminance range.
      let result = mix(value, "#000000", .15);
      for (let step = 16; luminance(result) > .055 && step <= 100; step++) result = mix(value, "#000000", step / 100);
      return result;
    };
    const background = darken(get("background", "#0b1017"));
    const artwork = themed && root.getPropertyValue("--chrome-theme-background-image").includes("url(");
    const panel = preferences.surface === "minimal" ? (artwork ? "#111821" : background) : darken(get("section", "#111821"));
    const target = preferences.surface === "minimal" ? (preferences.readability === "stronger" ? 10 : 7)
      : (preferences.readability === "stronger" ? 7 : 4.5);
    const text = readable(panel, get(preferences.surface === "minimal" ? "text" : "panel-text", "#e8edf3"), target);
    const overrides = {};
    if (preferences.tone === "darker" || preferences.surface === "minimal" || preferences.readability === "stronger") {
      overrides["--chrome-theme-background"] = background;
      overrides["--chrome-theme-section"] = panel;
      overrides["--chrome-theme-panel-text"] = text;
      overrides["--chrome-theme-text"] = preferences.surface === "minimal" && artwork ? text : readable(background, get("text", "#e8edf3"), target);
      overrides["--chrome-theme-link"] = readable(panel, get("link", "#67a8ff"), target);
      if (!themed) Object.assign(overrides, {"--bg": background, "--panel": panel, "--panel-2": panel, "--panel-3": panel, "--text": text});
      if (preferences.tone === "darker") {
        for (const name of ["frame", "toolbar"]) {
          const color = darken(get(name, "#151e29"));
          overrides[`--chrome-theme-${name}`] = color;
          overrides[`--chrome-theme-${name}-text`] = readable(color, get(`${name}-text`, "#e8edf3"), target);
          // Artwork keeps its authored foreground; only plain surfaces need a new pair.
          if (!body.classList.contains(`chrome-theme-${name}-art`)) overrides[`--chrome-theme-${name}-image-text`] = overrides[`--chrome-theme-${name}-text`];
        }
      }
    }
    const names = ["background", "section", "panel-text", "text", "frame", "toolbar", "frame-text", "toolbar-text", "frame-image-text", "toolbar-image-text", "link"].map(name => `--chrome-theme-${name}`)
      .concat(["--bg", "--panel", "--panel-2", "--panel-3", "--text"]);
    names.forEach(name => overrides[name] ? body.style.setProperty(name, overrides[name]) : body.style.removeProperty(name));
    body.style.colorScheme = Object.keys(overrides).length ? (luminance(panel) > .179 ? "light" : "dark") : "";
    body.style.setProperty("--appearance-halo", luminance(text) > .179 ? "#000000" : "#ffffff");
  }

  function apply(preferences) {
    const root = document.documentElement;
    const body = document.body;
    if (!body) return;
    Object.entries(preferences).forEach(([key, value]) => {
      root.dataset[`appearance${key[0].toUpperCase()}${key.slice(1)}`] = value;
      body.dataset[`appearance${key[0].toUpperCase()}${key.slice(1)}`] = value;
    });
    palette(preferences);
    document.querySelectorAll("input[name^=appearance]").forEach((input) => {
      const key = input.name === "appearanceTone" ? "tone"
        : input.name === "appearanceSurface" ? "surface" : "readability";
      input.checked = input.value === preferences[key];
    });
  }

  function save(preferences) {
    try { localStorage.setItem(KEY, JSON.stringify(preferences)); } catch (_) {}
    apply(preferences);
  }

  let preferences = read();
  apply(preferences);
  document.addEventListener("change", (event) => {
    const input = event.target.closest("input[name^=appearance]");
    if (!input || !input.checked) return;
    const key = input.name === "appearanceTone" ? "tone"
      : input.name === "appearanceSurface" ? "surface" : "readability";
    if (!valid[key].has(input.value)) return;
    preferences = { ...preferences, [key]: input.value };
    save(preferences);
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== KEY && event.key !== null) return;
    preferences = read();
    apply(preferences);
  });
  // Theme polling changes root palette tokens and body artwork classes. Reinterpret
  // the fresh authored palette without modifying it or fetching images again.
  new MutationObserver(() => palette(preferences)).observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
  new MutationObserver(() => palette(preferences)).observe(document.body, { attributes: true, attributeFilter: ["class"] });
})();
