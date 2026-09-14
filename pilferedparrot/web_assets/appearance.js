/* Shared palette and controls. Durable choices belong to the application;
   appearance-sync.js keeps separate browser profiles on the same settings. */
(function () {
  "use strict";
  const defaults = { tone: "darker", surface: "minimal", readability: "standard" };
  const valid = {
    tone: new Set(["original", "darker"]),
    surface: new Set(["minimal", "balanced", "maximal"]),
    readability: new Set(["standard", "stronger"]),
  };
  let preferences = { ...defaults };
  const normalize = value => Object.fromEntries(Object.keys(defaults).map(key => [
    key, valid[key].has(value?.[key]) ? value[key] : defaults[key],
  ]));
  const channels = color => color.slice(1).match(/../g).map(value => parseInt(value, 16));
  const hex = rgb => `#${rgb.map(value => Math.round(value).toString(16).padStart(2, "0")).join("")}`;
  const luminance = color => channels(color).map(value => value / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  const mix = (from, to, amount) => hex(channels(from).map((value, index) =>
    value + (channels(to)[index] - value) * amount));
  function readable(background, requested, target = 4.5) {
    const light = luminance(background);
    const extreme = light > .179 ? "#000000" : "#ffffff";
    const ratio = text => (Math.max(light, luminance(text)) + .05) / (Math.min(light, luminance(text)) + .05);
    for (let step = 0; step <= 100; step++) {
      const candidate = mix(requested, extreme, step / 100);
      if (ratio(candidate) >= target) return candidate;
    }
    return extreme;
  }
  function palette() {
    const body = document.body;
    const root = getComputedStyle(document.documentElement);
    const themed = body.classList.contains("chrome-theme");
    const get = (name, fallback) => {
      const value = root.getPropertyValue(`--chrome-theme-${name}`).trim();
      return themed && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
    };
    const darken = value => {
      if (preferences.tone !== "darker") return value;
      let result = mix(value, "#071c2b", .15);
      for (let step = 16; luminance(result) > .055 && step <= 100; step++) {
        result = mix(value, "#071c2b", step / 100);
      }
      return result;
    };
    const background = darken(get("background", "#102637"));
    let authoredPanel = get("section", "#214c68");
    // Opposite light/dark section fills cannot retain readable text when made
    // translucent over the canvas. Keep the canvas tone across all surfaces.
    if ((luminance(authoredPanel) > .179) !== (luminance(background) > .179)) {
      authoredPanel = background;
    }
    const tint = luminance(authoredPanel) > .45 ? "#c5deec" : "#285b78";
    // Surface weight changes opacity, never the palette. In particular, Minimal
    // must not replace theme blue with a hard-coded black field color.
    const panel = darken(mix(authoredPanel, tint, .28));
    const contrast = preferences.readability === "stronger" ? 7 : 6;
    const text = readable(panel, get("panel-text", "#edf6fc"), contrast);
    const muted = readable(panel, mix(text, panel, .18), contrast);
    const accent = readable(panel, get("link", "#b4e2ff"), contrast);
    const action = mix(panel, luminance(panel) > .179 ? "#246080" : "#4285ae", .7);
    const halo = luminance(text) > .179 ? "#061b2b" : "#ffffff";
    const artwork = themed && root.getPropertyValue("--chrome-theme-background-image").includes("url(");
    // Theme palette contrast alone says nothing about a bright star underneath
    // translucent text. Bound the artwork's brightest (or darkest) pixels too.
    let veil = "transparent";
    if (artwork) {
      const worst = luminance(text) > .179 ? "#ffffff" : "#000000";
      const target = preferences.readability === "stronger" ? 7 : 4.5;
      let opacity = 0;
      for (; opacity < 100; opacity++) {
        const light = luminance(mix(worst, halo, opacity / 100));
        if ([text, muted, accent].every(color =>
          (Math.max(light, luminance(color)) + .05) /
          (Math.min(light, luminance(color)) + .05) >= target)) break;
      }
      veil = `${halo}${Math.round(opacity * 2.55).toString(16).padStart(2, "0")}`;
    }
    const shadow = artwork
      ? (preferences.readability === "stronger" ? `0 1px 3px ${halo}, 0 0 6px ${halo}66` : `0 1px 2px ${halo}99`)
      : "none";
    const values = {
      "--bg": background, "--panel": panel, "--panel-2": panel, "--panel-3": panel,
      "--text": text, "--muted": muted, "--faint": muted, "--surface-muted": muted,
      "--blue": accent, "--blue-strong": accent,
      "--selection-tint": mix(panel, accent, .14),
      "--action-bg": action, "--action-text": readable(action, "#ffffff"),
      "--surface-text-shadow": shadow, "--appearance-halo": halo,
      "--artwork-veil": veil,
      "--chrome-theme-background": background, "--chrome-theme-section": panel,
      "--chrome-theme-panel-text": text, "--chrome-theme-text": text,
      "--chrome-theme-link": accent,
    };
    Object.entries(values).forEach(([key, value]) => body.style.setProperty(key, value));
    body.style.colorScheme = luminance(panel) > .179 ? "light" : "dark";
  }
  function apply(value) {
    preferences = normalize(value);
    for (const root of [document.documentElement, document.body]) {
      Object.entries(preferences).forEach(([key, value]) => {
        root.dataset[`appearance${key[0].toUpperCase()}${key.slice(1)}`] = value;
      });
    }
    palette();
    document.querySelectorAll("input[name^=appearance]").forEach(input => {
      const key = input.name === "appearanceTone" ? "tone"
        : input.name === "appearanceSurface" ? "surface" : "readability";
      input.checked = input.value === preferences[key];
    });
  }
  globalThis.PilferedParrotAppearance = Object.freeze({ apply, current: () => ({ ...preferences }) });
  apply(defaults);
  document.addEventListener("change", event => {
    const input = event.target.closest("input[name^=appearance]");
    if (!input || !input.checked) return;
    const key = input.name === "appearanceTone" ? "tone"
      : input.name === "appearanceSurface" ? "surface" : "readability";
    if (!valid[key].has(input.value)) return;
    apply({ ...preferences, [key]: input.value });
    document.dispatchEvent(new CustomEvent("ppi-appearance-change", { detail: { key, value: input.value } }));
  });
  // The authored theme stays on the root; derived blue surfaces live on body.
  new MutationObserver(palette).observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
  new MutationObserver(palette).observe(document.body, { attributes: true, attributeFilter: ["class"] });
})();
