(function (global) {
  "use strict";

  var timers = new WeakMap();

  function restoreSelection(selection, ranges) {
    if (!selection || !ranges) return;
    try {
      selection.removeAllRanges();
      ranges.forEach(function (range) { selection.addRange(range); });
    } catch (_error) {}
  }

  function fallbackCopy(value) {
    var active = document.activeElement;
    var selection = global.getSelection ? global.getSelection() : null;
    var ranges = [];
    if (selection) for (var index = 0; index < selection.rangeCount; index += 1) ranges.push(selection.getRangeAt(index).cloneRange());
    var textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    var copied = false;
    var container = (active && active.closest && active.closest("dialog[open]")) || document.body;
    container.appendChild(textarea);
    try {
      textarea.focus();
      textarea.select();
      copied = document.execCommand("copy");
    } finally {
      textarea.remove();
      restoreSelection(selection, ranges);
      if (active && active.isConnected && typeof active.focus === "function") active.focus();
    }
    if (!copied) throw new Error("Copy command was rejected");
  }

  function writeClipboard(value) {
    if (global.navigator && global.navigator.clipboard && typeof global.navigator.clipboard.writeText === "function") {
      return Promise.resolve().then(function () { return global.navigator.clipboard.writeText(value); })
        .catch(function () { fallbackCopy(value); });
    }
    return Promise.resolve().then(function () { fallbackCopy(value); });
  }

  function feedback(button, copied, token) {
    var state = timers.get(button);
    if (!state || state.token !== token) return;
    button.textContent = copied ? "Copied" : "Copy failed";
    button.setAttribute("aria-label", copied ? "Copied" : "Copy failed");
    button.dataset.copyState = copied ? "success" : "error";
    state.timer = global.setTimeout(function () {
      if (state.token !== token) return;
      button.textContent = state.text;
      if (state.label) button.setAttribute("aria-label", state.label); else button.removeAttribute("aria-label");
      button.removeAttribute("data-copy-state");
      timers.delete(button);
    }, 1400);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-copy-code]");
    if (!button) return;
    event.preventDefault();
    var state = timers.get(button);
    if (state && state.timer) global.clearTimeout(state.timer);
    if (!state) state = { text: button.textContent || "Copy", label: button.getAttribute("aria-label"), token: 0, timer: null };
    state.token += 1;
    var token = state.token;
    timers.set(button, state);
    var code = button.closest(".code-block");
    var node = code && code.querySelector("pre code");
    if (!node) { feedback(button, false, token); return; }
    writeClipboard(node.textContent).then(function () {
      feedback(button, true, token);
    }).catch(function () {
      feedback(button, false, token);
    });
  });
}(window));
