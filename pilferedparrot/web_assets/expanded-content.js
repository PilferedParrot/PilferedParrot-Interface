(function (global) {
  "use strict";

  var CONTENT_SELECTOR = ".message-content, .chat-message-body";
  var TARGET_SELECTOR = ".table-scroll, .code-block";
  var dialog;
  var dialogContent;
  var returnFocus;
  var resizeObserver;
  var mutationObserver;
  var scheduled = false;
  var viewerToken = 0;
  var fullscreenOwnerToken = 0;
  var observedTargets = [];

  function targetTitle(target) {
    return target.classList.contains("table-scroll") ? "Expanded table" : "Expanded code";
  }

  function ensureDialog() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.id = "markdownExpansionDialog";
    dialog.setAttribute("aria-labelledby", "markdownExpansionTitle");
    dialog.innerHTML = '<div class="markdown-expansion-card">' +
      '<div class="dialog-heading markdown-expansion-heading"><h2 id="markdownExpansionTitle">Expanded content</h2>' +
      '<button type="button" class="dialog-close markdown-expansion-close" aria-label="Close expanded content" title="Close expanded content">×</button></div>' +
      '<div class="markdown-expansion-content"></div></div>';
    document.body.appendChild(dialog);
    dialogContent = dialog.querySelector(".markdown-expansion-content");
    dialog.querySelector(".markdown-expansion-close").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("close", function () {
      dialogContent.replaceChildren();
      var target = returnFocus;
      returnFocus = null;
      releaseFullscreen().then(function () {
        // Exiting fullscreen can make the original block overflow again.
        // Let ResizeObserver restore its control before returning focus.
        schedule();
        global.requestAnimationFrame(function () {
          if (!dialog.open && target && target.isConnected) target.focus();
        });
      });
    });
  }

  function overflows(target) {
    var measured = target.classList.contains("table-scroll") ? target : target.querySelector("pre");
    return Boolean(measured && measured.scrollWidth > measured.clientWidth + 1);
  }

  function makeReadOnlyCopy(target) {
    var copy = target.cloneNode(true);
    copy.classList.remove("runnable");
    copy.removeAttribute("id");
    copy.querySelectorAll("[id]").forEach(function (node) { node.removeAttribute("id"); });
    copy.querySelectorAll("button, [data-run-command], .markdown-expand-toolbar").forEach(function (node) {
      node.remove();
    });
    return copy;
  }

  function open(target, button) {
    ensureDialog();
    viewerToken += 1;
    var token = viewerToken;
    returnFocus = button;
    var rendered = document.createElement("div");
    rendered.className = "message-content markdown-expansion-rendered";
    rendered.appendChild(makeReadOnlyCopy(target));
    dialogContent.replaceChildren(rendered);
    dialog.querySelector("#markdownExpansionTitle").textContent = targetTitle(target);
    requestFullscreen(token);
  }

  function showDialog(token) {
    if (token !== viewerToken || dialog.open) return;
    dialog.showModal();
    dialog.querySelector(".markdown-expansion-close").focus();
  }

  function requestFullscreen(token) {
    var root = document.documentElement;
    if (!root.requestFullscreen || document.fullscreenElement) {
      showDialog(token);
      return;
    }
    var request;
    try {
      request = root.requestFullscreen();
    } catch (_error) {
      showDialog(token);
      return;
    }
    Promise.resolve(request).then(function () {
      if (token !== viewerToken) {
        if (document.fullscreenElement === root) Promise.resolve(document.exitFullscreen()).catch(function () {});
        return;
      }
      fullscreenOwnerToken = token;
      showDialog(token);
    }).catch(function () {
      /* Browsers may reject fullscreen requests; the viewport dialog remains usable. */
      showDialog(token);
    });
  }

  function releaseFullscreen() {
    var root = document.documentElement;
    var token = fullscreenOwnerToken;
    fullscreenOwnerToken = 0;
    if (token && document.fullscreenElement === root && document.exitFullscreen) {
      return Promise.resolve(document.exitFullscreen()).catch(function () {});
    }
    return Promise.resolve();
  }

  function ensureRegion(target) {
    var region = target.parentElement;
    if (!region || !region.classList.contains("markdown-expand-region")) {
      region = document.createElement("div");
      region.className = "markdown-expand-region";
      target.parentNode.insertBefore(region, target);
      region.appendChild(target);
    }
    var toolbar = region.querySelector(":scope > .markdown-expand-toolbar");
    if (!toolbar) {
      toolbar = document.createElement("div");
      toolbar.className = "markdown-expand-toolbar";
      region.insertBefore(toolbar, target);
    }
    var button = toolbar.querySelector(".markdown-expand-button");
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "markdown-expand-button";
      button.textContent = "⛶";
      button.setAttribute("aria-label", "Expand to full screen");
      button.title = "Expand to full screen";
      button.addEventListener("click", function () { open(target, button); });
      toolbar.appendChild(button);
    }
    return button;
  }

  function update(target) {
    if (!target.isConnected) return;
    var button = ensureRegion(target);
    button.hidden = !overflows(target);
    button.parentElement.hidden = button.hidden;
    if (resizeObserver && observedTargets.indexOf(target) < 0) {
      observedTargets.push(target);
      resizeObserver.observe(target);
    }
  }

  function scan(root) {
    if (!root || !root.querySelectorAll) return;
    var targets = [];
    if (root.matches && root.matches(TARGET_SELECTOR)) targets.push(root);
    Array.prototype.push.apply(targets, root.querySelectorAll(TARGET_SELECTOR));
    targets.forEach(update);
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    global.requestAnimationFrame(function () {
      scheduled = false;
      observedTargets = observedTargets.filter(function (target) {
        if (target.isConnected) return true;
        resizeObserver.unobserve(target);
        return false;
      });
      document.querySelectorAll(CONTENT_SELECTOR).forEach(function (root) {
        if (!dialog.contains(root)) scan(root);
      });
    });
  }

  function start() {
    ensureDialog();
    resizeObserver = new ResizeObserver(schedule);
    mutationObserver = new MutationObserver(schedule);
    mutationObserver.observe(document.body, { childList: true, characterData: true, subtree: true });
    document.addEventListener("fullscreenchange", function () {
      if (fullscreenOwnerToken && document.fullscreenElement !== document.documentElement && dialog.open) dialog.close();
    });
    global.addEventListener("resize", schedule);
    schedule();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}(window));
