"""Native desktop, browser-window, and browser-theme integration."""

from __future__ import annotations

import json
import ipaddress
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


CHROMIUM_BROWSER_CANDIDATES = (
    "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
)
CHROME_THEME_GALLERY_URL = "https://chromewebstore.google.com/category/themes"
CHROME_THEME_ID = re.compile(r"[a-p]{32}")
CHROME_THEME_COLOR_KEYS = (
    "frame", "toolbar", "ntp_background", "ntp_text", "ntp_link", "ntp_section",
    "ntp_section_text", "tab_background_text", "bookmark_text", "toolbar_text",
)
CHROME_THEME_IMAGE_KEYS = (
    "theme_frame", "theme_frame_overlay", "theme_toolbar", "theme_ntp_background",
    "theme_ntp_attribution",
)
CHROME_THEME_IMAGE_MAX_BYTES = 16 * 1024 * 1024
_DISCOVER_BROWSER = object()
WINDOWS = sys.platform == "win32"


def _windows_browser_candidates() -> list[Path]:
    """Return installed Windows browser paths in the preferred order."""
    roots: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    candidates: list[Path] = []
    for root in roots:
        candidates.extend((
            root / "Google/Chrome/Application/chrome.exe",
            root / "Chromium/Application/chrome.exe",
            root / "Chromium/Application/chromium.exe",
        ))
    for root in roots:
        candidates.append(root / "Microsoft/Edge/Application/msedge.exe")
    return candidates


def chromium_browser() -> str | None:
    """Return the same preferred Chrome-family browser used by the app launcher."""
    if not WINDOWS:
        return next((
            path for candidate in CHROMIUM_BROWSER_CANDIDATES
            if (path := shutil.which(candidate))
        ), None)
    for candidate in (
        "chrome.exe", "chrome", "chromium.exe", "chromium", "msedge.exe", "msedge",
    ):
        if path := shutil.which(candidate):
            return path
    return next((str(path) for path in _windows_browser_candidates() if path.is_file()), None)


def _active_x11_window() -> str | None:
    """Return the active X11 window ID when xdotool can identify one."""
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        return None
    try:
        completed = subprocess.run(
            [xdotool, "getactivewindow"], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    window_id = completed.stdout.strip()
    if completed.returncode != 0 or not window_id.isascii() \
            or not window_id.isdecimal() or int(window_id) <= 0:
        return None
    return window_id


def _activate_native_chooser(process: subprocess.Popen[str], title: str) -> None:
    """Best-effort raise a chooser without making X11 helpers mandatory."""
    xdotool = shutil.which("xdotool")
    if xdotool is None:
        return
    try:
        found = subprocess.run(
            [xdotool, "search", "--sync", "--all", "--pid", str(process.pid),
             "--name", f"^{re.escape(title)}$"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=2,
        )
        window_id = next((item for item in found.stdout.split() if item.isdecimal()), None)
        if found.returncode != 0 or window_id is None:
            return
        for action in ("windowactivate", "windowraise"):
            action_options = ["--sync"] if action == "windowactivate" else []
            try:
                subprocess.run(
                    [xdotool, action, *action_options, window_id], check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
    except (OSError, subprocess.TimeoutExpired, AttributeError):
        return


def select_project_directory(
    initial: Any, *, normalize: Callable[[str], Path],
) -> Path | None:
    """Open a native folder chooser and return its validated selection."""
    try:
        starting_directory = Path(str(initial or "")).expanduser().resolve()
    except (OSError, RuntimeError):
        starting_directory = Path.home().resolve()
    if not starting_directory.is_dir():
        starting_directory = Path.home().resolve()

    if WINDOWS:
        chooser = shutil.which("powershell") or shutil.which("pwsh")
        if chooser is None:
            raise RuntimeError(
                "No native folder chooser is available; PowerShell is required, "
                "or enter the project folder path manually."
            )
        environment = os.environ.copy()
        environment["PILFEREDPARROT_CHOOSER_INITIAL"] = str(starting_directory)
        script = (
            "$ErrorActionPreference='Stop'; "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description='Choose project folder'; "
            "$dialog.SelectedPath=$env:PILFEREDPARROT_CHOOSER_INITIAL; "
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
            "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
            "{[Console]::Write($dialog.SelectedPath)}"
        )
        try:
            process = subprocess.Popen(
                [chooser, "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise RuntimeError("The native folder chooser could not be opened.") from error
        stdout, _stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError("The native folder chooser could not be opened.")
        selected = stdout.rstrip("\r\n")
        return normalize(selected) if selected else None

    parent_window = _active_x11_window()
    if chooser := shutil.which("zenity"):
        starting_value = str(starting_directory)
        if not starting_value.endswith(os.sep):
            starting_value += os.sep
        command = [
            chooser, "--file-selection", "--directory", "--modal",
            "--title=Choose project folder", f"--filename={starting_value}",
        ]
        if parent_window is not None:
            command.append(f"--attach={parent_window}")
    elif chooser := shutil.which("kdialog"):
        command = [
            chooser, "--getexistingdirectory", str(starting_directory),
            "--title", "Choose project folder",
        ]
        if parent_window is not None:
            command[1:1] = ["--attach", parent_window]
    else:
        raise RuntimeError(
            "No native folder chooser is available; install zenity or kdialog, "
            "or enter the project folder path manually."
        )

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as error:
        raise RuntimeError("The native folder chooser could not be opened.") from error
    _activate_native_chooser(process, "Choose project folder")
    stdout, _stderr = process.communicate()
    if process.returncode == 1:
        return None
    if process.returncode != 0:
        raise RuntimeError("The native folder chooser could not be opened.")
    selected = stdout.rstrip("\r\n")
    return normalize(selected) if selected else None


def persistent_browser_profile() -> Path:
    """Match the persistent profile path in bin/pilferedparrot-app-browser."""
    if WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA")
        state_root = (
            Path(local_app_data).expanduser() if local_app_data
            else Path.home() / "AppData/Local"
        )
        return state_root / "PilferedParrot/chrome-profile"
    configured = os.environ.get("XDG_STATE_HOME")
    state_root = Path(configured).expanduser() if configured else Path.home() / ".local/state"
    return state_root / "pilferedparrot/chrome-profile"


def _chrome_theme_color(value: Any) -> str | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    channels: list[int] = []
    for channel in value[:3]:
        if isinstance(channel, bool) or not isinstance(channel, (int, float)):
            return None
        channels.append(max(0, min(255, round(channel))))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _chrome_theme_name(pack: Path, manifest: dict[str, Any]) -> str:
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        return "Chrome theme"
    message = re.fullmatch(r"__MSG_([^_].*)__", name)
    locale = manifest.get("default_locale")
    if message and isinstance(locale, str) and re.fullmatch(r"[A-Za-z0-9_-]+", locale):
        messages_path = (pack / "_locales" / locale / "messages.json").resolve()
        try:
            if messages_path.is_relative_to(pack) and messages_path.stat().st_size <= 256_000:
                messages = json.loads(messages_path.read_text(encoding="utf-8"))
                translated = messages.get(message.group(1), {}).get("message")
                if isinstance(translated, str) and translated.strip():
                    return translated.strip()[:120]
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            pass
    return name.strip()[:120]


def _chrome_theme_image(pack: Path, value: Any) -> Path | None:
    """Resolve a manifest image while keeping reads inside the unpacked theme."""
    if isinstance(value, dict):
        # Chrome theme images may be keyed by scale (for example {"100": path}).
        choices = []
        for scale, image in value.items():
            try:
                choices.append((abs(float(scale) - 100), float(scale), image))
            except (TypeError, ValueError):
                continue
        value = min(choices, key=lambda item: (item[0], -item[1]))[2] if choices else None
    if not isinstance(value, str) or not value or len(value) > 1024:
        return None
    try:
        candidate = (pack / value).resolve()
        if (
            candidate.is_relative_to(pack) and candidate.is_file()
            and candidate.stat().st_size <= CHROME_THEME_IMAGE_MAX_BYTES
            and candidate.suffix.lower() in {".gif", ".jpeg", ".jpg", ".png", ".webp"}
        ):
            return candidate
    except (OSError, RuntimeError):
        pass
    return None


def selected_chrome_theme() -> tuple[dict[str, Any], Path | None]:
    """Read the active Chrome theme without trusting extension-controlled paths."""
    inactive: dict[str, Any] = {"active": False}
    profile = persistent_browser_profile().resolve()
    preferences_path = profile / "Default/Preferences"
    try:
        if preferences_path.stat().st_size > 20 * 1024 * 1024:
            return inactive, None
        preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
        selected = preferences.get("extensions", {}).get("theme", {})
        theme_id = selected.get("id")
        pack_value = selected.get("pack")
        if not isinstance(theme_id, str) or not CHROME_THEME_ID.fullmatch(theme_id):
            return inactive, None
        if not isinstance(pack_value, str):
            return inactive, None
        extensions_root = (profile / "Default/Extensions").resolve()
        extension_root = (extensions_root / theme_id).resolve()
        pack = Path(pack_value).expanduser().resolve()
        if (
            not extension_root.is_relative_to(extensions_root)
            or not pack.is_relative_to(extension_root) or not pack.is_dir()
        ):
            return inactive, None
        manifest_path = (pack / "manifest.json").resolve()
        if not manifest_path.is_relative_to(pack) or manifest_path.stat().st_size > 1_000_000:
            return inactive, None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        theme = manifest.get("theme")
        if not isinstance(theme, dict):
            return inactive, None
        raw_colors = theme.get("colors") if isinstance(theme.get("colors"), dict) else {}
        colors = {
            key: color for key in CHROME_THEME_COLOR_KEYS
            if (color := _chrome_theme_color(raw_colors.get(key))) is not None
        }
        raw_images = theme.get("images") if isinstance(theme.get("images"), dict) else {}
        image_paths = {
            key: image_path for key in CHROME_THEME_IMAGE_KEYS
            if (image_path := _chrome_theme_image(pack, raw_images.get(key))) is not None
        }
        properties = theme.get("properties") if isinstance(theme.get("properties"), dict) else {}
        alignment = properties.get("ntp_background_alignment", "center")
        repeat = properties.get("ntp_background_repeat", "no-repeat")
        if not isinstance(alignment, str) or any(
            token not in {"bottom", "center", "left", "right", "top"}
            for token in alignment.split()
        ) or len(alignment.split()) > 2:
            alignment = "center"
        if not isinstance(repeat, str) or repeat not in {"no-repeat", "repeat", "repeat-x", "repeat-y"}:
            repeat = "no-repeat"
        version = re.sub(r"[^A-Za-z0-9._-]", "", str(manifest.get("version") or ""))[:40]
        public = {
            "active": True,
            "id": theme_id,
            "version": version,
            "name": _chrome_theme_name(pack, manifest),
            "colors": colors,
            "background": "theme_ntp_background" in image_paths,
            "background_alignment": alignment,
            "background_repeat": repeat,
        }
        image_urls = {
            "theme_frame": "frame_url", "theme_toolbar": "toolbar_url",
            "theme_frame_overlay": "frame_overlay_url",
            "theme_ntp_background": "background_url", "theme_ntp_attribution": "attribution_url",
        }
        for image_key, public_key in image_urls.items():
            if image_key in image_paths:
                endpoint = "/api/browser/theme/background" if image_key == "theme_ntp_background" \
                    else f"/api/browser/theme/image/{image_key}"
                public[public_key] = f"{endpoint}?v={theme_id}-{version}"
        return public, image_paths.get("theme_ntp_background")
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        return inactive, None


def chrome_theme_version(theme: dict[str, Any]) -> str | None:
    """Return the stable cache-busting token for a selected theme."""
    theme_id = theme.get("id")
    version = theme.get("version")
    if not isinstance(theme_id, str) or not CHROME_THEME_ID.fullmatch(theme_id):
        return None
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{0,40}", version):
        return None
    return f"{theme_id}-{version}"


def browser_url(
    url: str, capability: str, *, api_generation: int,
    asset_version: str, runtime_version: str,
) -> str:
    """Deliver a dashboard capability in a non-referrer URL fragment."""
    return (
        f"{url}/?generation={api_generation}&assets={asset_version}"
        f"&runtime={runtime_version}#capability={capability}"
    )


def native_app_url(url: str) -> str:
    """Mark a URL as a browser app window without exposing the marker to servers."""
    separator = "&" if "#" in url else "#"
    return f"{url}{separator}native-window=1"


def open_browser(url: str) -> bool:
    """Open a URL through the operator's configured desktop browser."""
    return webbrowser.open(url)


def _is_edge_browser(browser: str) -> bool:
    return Path(browser).stem.lower() in {"msedge", "microsoftedge"}


def _app_browser_profile(browser: str) -> Path:
    profile = persistent_browser_profile()
    if _is_edge_browser(browser):
        return profile.parent / "edge-profile"
    return profile


def open_app_browser(url: str, *, browser: str | None = None) -> bool:
    """Open *url* as a dedicated app window using the persistent Windows profile."""
    browser = browser or chromium_browser()
    if browser is None:
        return False
    profile = _app_browser_profile(browser)
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    started = time.monotonic()
    process = subprocess.Popen(
        [
            browser, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-mode",
            "--disable-session-crashed-bubble", "--start-maximized",
            "--class=pilferedparrot", f"--app={native_app_url(url)}",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
    )
    threading.Thread(
        target=_watch_app_browser, args=(process, url, started),
        name="pilferedparrot-app-browser", daemon=True,
    ).start()
    return True


def _watch_app_browser(
    process: subprocess.Popen[Any], url: str, started: float,
) -> None:
    """Notify the loopback server when the standalone app window really closed."""
    try:
        process.wait()
    except (OSError, ValueError):
        return
    # A process that exits during startup did not represent a usable window.
    if time.monotonic() - started >= 2:
        notify_window_closed(url)


class WindowsAppBrowser(webbrowser.BaseBrowser):
    """webbrowser controller suitable for registering the native Windows app window."""

    def __init__(self, browser: str | None = None):
        super().__init__(name="pilferedparrot-windows-app")
        self.browser = browser

    def open(self, url: str, new: int = 0, autoraise: bool = True) -> bool:
        del new, autoraise
        return open_app_browser(url, browser=self.browser)


def notify_window_closed(
    browser_url: str, *, opener: Callable[..., Any] | None = None,
    is_loopback: Callable[[str], bool] | None = None,
) -> bool:
    """Tell the exact loopback server instance that its main app window closed."""
    try:
        parsed = urlparse(browser_url)
        if parsed.hostname is None:
            return False
        if is_loopback is None:
            try:
                local_host = parsed.hostname == "localhost" \
                    or ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                local_host = False
        else:
            local_host = is_loopback(parsed.hostname)
        if parsed.scheme != "http" or not local_host \
                or parsed.username is not None or parsed.password is not None:
            return False
        tokens = parse_qs(parsed.fragment, keep_blank_values=True).get("capability", [])
        if len(tokens) != 1 or not tokens[0]:
            return False
        request = Request(
            f"http://{parsed.netloc}/api/window/close",
            data=b'{"window_id":"main"}',
            headers={
                "Content-Type": "application/json",
                "X-PilferedParrot-Capability": tokens[0],
                "Origin": f"http://{parsed.netloc}",
            },
            method="POST",
        )
        with (opener or urlopen)(request, timeout=2) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


class NativeIntegration:
    """Own native browser processes, profiles, and their GUI lifecycle hooks."""

    def __init__(self, revoke_capability: Callable[[str | None], None]):
        self.revoke_capability = revoke_capability
        self.chat_window_capability: str | None = None
        self.chat_window_provider: str | None = None
        self.chat_window_lock = threading.RLock()
        self.chat_window_process: subprocess.Popen[bytes] | None = None
        self.chat_window_profile: Path | None = None
        self.provider_windows_lock = threading.RLock()
        self.provider_windows: dict[str, dict[str, Any]] = {}
        self.native_window_lock = threading.RLock()
        self.native_window_adapter: Any = None
        self.native_window_markers: dict[str, str] = {}
        self.native_windows: dict[str, Any] = {}

    def native_window_action(
        self, window_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind or control the native window owned by one capability context."""
        action = payload.get("action")
        if not isinstance(action, str) or action not in {
            "prepare", "bind", "minimize", "maximize", "close", "drag", "resize",
        }:
            raise ValueError("native window action is invalid")
        allowed_fields = {"action", "direction"} if action == "resize" else {"action"}
        if set(payload) - allowed_fields:
            raise ValueError("native window action fields are invalid")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", window_id):
            raise ValueError("native window id is invalid")
        with self.native_window_lock:
            if action == "prepare":
                marker = f"PilferedParrot Native {secrets.token_hex(16)}"
                self.native_window_markers[window_id] = marker
                return {"ok": True, "marker": marker}
            if action == "bind":
                marker = self.native_window_markers.get(window_id)
                if marker is None:
                    raise ValueError("native window binding was not prepared")
                if self.native_window_adapter is None:
                    from .native_window import NativeWindowAdapter
                    self.native_window_adapter = NativeWindowAdapter()
                window = self.native_window_adapter.bind_active_window(marker)
                if window is None:
                    return {"ok": True, "supported": False}
                self.native_window_markers.pop(window_id, None)
                self.native_windows[window_id] = window
                return {"ok": True, "supported": True}
            window = self.native_windows.get(window_id)
            if window is None:
                return {"ok": False, "supported": False}
            if action == "minimize":
                ok = window.minimize()
            elif action == "maximize":
                ok = window.maximize_or_restore()
            elif action == "close":
                ok = window.close()
                if ok:
                    self.native_windows.pop(window_id, None)
            elif action == "drag":
                ok = window.drag()
            else:
                direction = payload.get("direction")
                if not isinstance(direction, str):
                    raise ValueError("native window resize direction is invalid")
                ok = window.resize(direction)
            return {"ok": bool(ok), "supported": True}

    def forget_native_window(self, window_id: str) -> None:
        with self.native_window_lock:
            self.native_window_markers.pop(window_id, None)
            self.native_windows.pop(window_id, None)

    @staticmethod
    def window_number(payload: dict[str, Any], name: str, minimum: int) -> int:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"chat window {name} is invalid")
        number = int(value)
        if number < minimum or number > 32_768:
            raise ValueError(f"chat window {name} is invalid")
        return number

    def _clean_chat_window_profile(self) -> None:
        profile = self.chat_window_profile
        self.chat_window_profile = None
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)

    def _watch_chat_window(self, process: subprocess.Popen[bytes]) -> None:
        process.wait()
        with self.chat_window_lock:
            if self.chat_window_process is process:
                self.chat_window_process = None
                self.revoke_capability(self.chat_window_capability)
                self.chat_window_capability = None
                self.chat_window_provider = None
                self._clean_chat_window_profile()
                self.forget_native_window("chat")

    def _watch_provider_window(self, launch_id: str, process: subprocess.Popen[bytes]) -> None:
        process.wait()
        with self.provider_windows_lock:
            record = self.provider_windows.get(launch_id)
            if record is None or record.get("process") is not process:
                return
            self.provider_windows.pop(launch_id, None)
        try:
            origin = str(record.get("origin") or "").rstrip("/")
            window_id = str(record.get("window_id") or "")
            request = Request(
                f"{origin}/api/window/close",
                data=json.dumps({"window_id": window_id}, separators=(",", ":")).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-PilferedParrot-Capability": str(record.get("capability") or ""),
                    "Origin": origin,
                },
                method="POST",
            )
            with urlopen(request, timeout=2):
                pass
        except (OSError, ValueError):
            pass
        self.revoke_capability(record.get("capability"))
        self.forget_native_window(launch_id)
        profile = record.get("profile")
        if isinstance(profile, Path):
            shutil.rmtree(profile, ignore_errors=True)

    def open_provider_window(
        self, url: str, *, provider: str, model: str | None, cwd: Path | None,
        payload: dict[str, Any], issue_capability: Callable[..., str],
        browser: str | None | object = _DISCOVER_BROWSER,
    ) -> dict[str, Any]:
        width = self.window_number(payload, "width", 640)
        height = self.window_number(payload, "height", 480)
        left = self.window_number(payload, "left", -32_768)
        top = self.window_number(payload, "top", -32_768)
        if browser is _DISCOVER_BROWSER:
            browser = chromium_browser()
        if browser is None:
            browser_names = "Chrome, Chromium, or Edge" if WINDOWS else "Chrome or Chromium"
            raise RuntimeError(f"{browser_names} is required for another provider window")
        history_id = f"provider-{provider}"
        launch_id = uuid.uuid4().hex
        capability = issue_capability(
            "dashboard", window_id=launch_id, provider=provider, history_id=history_id,
        )
        profile = Path(tempfile.mkdtemp(prefix=f"pilferedparrot-{provider}-"))
        provider_url = (
            f"{url}#capability={capability}&provider={provider}"
            f"&window={history_id}&native-window=1"
        )
        if cwd is None:
            provider_url += "&pick=1"
        else:
            provider_url += f"&cwd={quote(str(cwd), safe='')}"
        if model:
            provider_url += f"&model={quote(model, safe='')}"
        try:
            process = subprocess.Popen(
                [
                    browser, f"--user-data-dir={profile}", "--no-first-run",
                    "--no-default-browser-check", "--disable-background-mode",
                    "--disable-session-crashed-bubble", "--start-maximized",
                    f"--class=pilferedparrot-{provider}",
                    f"--window-size={width},{height}", f"--window-position={left},{top}",
                    f"--app={provider_url}",
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            )
        except Exception:
            self.revoke_capability(capability)
            shutil.rmtree(profile, ignore_errors=True)
            raise
        with self.provider_windows_lock:
            self.provider_windows[launch_id] = {
                "provider": provider, "process": process, "profile": profile,
                "capability": capability, "origin": url.rstrip("/"), "model": model,
                "window_id": launch_id, "history_id": history_id,
            }
        threading.Thread(
            target=self._watch_provider_window, args=(launch_id, process),
            name=f"pilferedparrot-{provider}-window", daemon=True,
        ).start()
        return {"ok": True, "window_id": history_id, "launch_id": launch_id}

    def open_chat_window(
        self, url: str, *, provider: str, model: str, payload: dict[str, Any],
        issue_capability: Callable[..., str],
        browser: str | None | object = _DISCOVER_BROWSER,
    ) -> dict[str, Any]:
        width = self.window_number(payload, "width", 320)
        height = self.window_number(payload, "height", 240)
        left = self.window_number(payload, "left", -32_768)
        top = self.window_number(payload, "top", -32_768)
        with self.chat_window_lock:
            process = self.chat_window_process
            if process is not None and process.poll() is None \
                    and self.chat_window_provider == provider:
                wmctrl = shutil.which("wmctrl")
                if wmctrl:
                    subprocess.run(
                        [wmctrl, "-x", "-a", "pilferedparrot-chat"], check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                return {"ok": True, "existing": True}
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            self.chat_window_process = None
            self.revoke_capability(self.chat_window_capability)
            self.chat_window_capability = None
            self.chat_window_provider = None
            self._clean_chat_window_profile()
            if browser is _DISCOVER_BROWSER:
                browser = chromium_browser()
            if browser is None:
                browser_names = "Chrome, Chromium, or Edge" if WINDOWS else "Chrome or Chromium"
                raise RuntimeError(f"{browser_names} is required for the Chat window")
            profile = Path(tempfile.mkdtemp(prefix="pilferedparrot-chat-"))
            capability = issue_capability(
                "chat", window_id="chat", provider=provider, model=model,
            )
            separator = "&" if "#" in url else "#"
            chat_url = (
                f"{url}{separator}capability={capability}"
                f"&provider={quote(provider, safe='')}&model={quote(model, safe='')}"
                "&native-window=1"
            )
            try:
                process = subprocess.Popen(
                    [
                        browser, f"--user-data-dir={profile}", "--no-first-run",
                        "--no-default-browser-check", "--disable-background-mode",
                        "--disable-session-crashed-bubble", "--start-maximized",
                        "--class=pilferedparrot-chat",
                        f"--window-size={width},{height}", f"--window-position={left},{top}",
                        f"--app={chat_url}",
                    ],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                )
            except Exception:
                self.revoke_capability(capability)
                shutil.rmtree(profile, ignore_errors=True)
                raise
            self.chat_window_capability = capability
            self.chat_window_provider = provider
            self.chat_window_profile = profile
            self.chat_window_process = process
            threading.Thread(
                target=self._watch_chat_window, args=(process,),
                name="pilferedparrot-chat-window", daemon=True,
            ).start()
            return {"ok": True, "existing": False}

    def open_theme_gallery(
        self, browser: str | None | object = _DISCOVER_BROWSER,
    ) -> dict[str, Any]:
        if browser is _DISCOVER_BROWSER:
            browser = chromium_browser()
        if browser is None:
            raise RuntimeError("Chrome or Chromium is required to choose a browser theme")
        if _is_edge_browser(str(browser)):
            raise RuntimeError(
                "Chrome or Chromium is required to choose a browser theme; "
                "Microsoft Edge cannot install Chrome themes here"
            )
        profile = persistent_browser_profile()
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.Popen(
            [
                browser, f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-background-mode",
                "--new-window", "--start-maximized", CHROME_THEME_GALLERY_URL,
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
        )
        return {"ok": True}

    @staticmethod
    def browser_theme() -> dict[str, Any]:
        theme, _background = selected_chrome_theme()
        return theme

    @staticmethod
    def chrome_theme_background(
        theme_version: str | None = None,
    ) -> tuple[bytes, str] | None:
        return NativeIntegration.chrome_theme_image(
            "theme_ntp_background", theme_version=theme_version,
        )

    @staticmethod
    def chrome_theme_image(
        image_key: str, *, theme_version: str | None = None,
    ) -> tuple[bytes, str] | None:
        """Return one allowlisted image from the selected theme."""
        if image_key not in CHROME_THEME_IMAGE_KEYS:
            return None
        selected_theme, background = selected_chrome_theme()
        if theme_version is not None and chrome_theme_version(selected_theme) != theme_version:
            return None
        if background is None and image_key == "theme_ntp_background":
            return None
        # Re-read the selected pack and resolve the requested manifest image.
        profile = persistent_browser_profile().resolve()
        try:
            preferences_path = profile / "Default/Preferences"
            if preferences_path.stat().st_size > 20 * 1024 * 1024:
                return None
            preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
            selected = preferences.get("extensions", {}).get("theme", {})
            theme_id = selected.get("id")
            pack = Path(str(selected.get("pack"))).expanduser().resolve()
            extensions_root = (profile / "Default/Extensions").resolve()
            extension_root = (extensions_root / str(theme_id)).resolve()
            if not isinstance(theme_id, str) or not CHROME_THEME_ID.fullmatch(theme_id):
                return None
            if not extension_root.is_relative_to(extensions_root) or not pack.is_relative_to(extension_root) or not pack.is_dir():
                return None
            manifest_path = (pack / "manifest.json").resolve()
            if not manifest_path.is_relative_to(pack) or manifest_path.stat().st_size > 1_000_000:
                return None
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if theme_version is not None:
                current_version = re.sub(
                    r"[^A-Za-z0-9._-]", "", str(manifest.get("version") or "")
                )[:40]
                if f"{theme_id}-{current_version}" != theme_version:
                    return None
            theme = manifest.get("theme") if isinstance(manifest, dict) else None
            images = theme.get("images", {}) if isinstance(theme, dict) else {}
            path = _chrome_theme_image(pack, images.get(image_key) if isinstance(images, dict) else None)
            if path is None:
                return None
            content_types = {
                ".gif": "image/gif", ".jpeg": "image/jpeg", ".jpg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
            }
            return path.read_bytes(), content_types[path.suffix.lower()]
        except (OSError, ValueError, json.JSONDecodeError, AttributeError, RuntimeError, KeyError):
            return None

    def shutdown(self, *, deadline: float | None = None, timeout: float = 3) -> None:
        if deadline is None:
            deadline = time.monotonic() + timeout
        with self.native_window_lock:
            self.native_window_markers.clear()
            self.native_windows.clear()
        with self.chat_window_lock:
            process = self.chat_window_process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            self.chat_window_process = None
            self.revoke_capability(self.chat_window_capability)
            self.chat_window_capability = None
            self.chat_window_provider = None
            self._clean_chat_window_profile()
        with self.provider_windows_lock:
            provider_windows = list(self.provider_windows.values())
            self.provider_windows.clear()
        for record in provider_windows:
            process = record.get("process")
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            self.revoke_capability(record.get("capability"))
            profile = record.get("profile")
            if isinstance(profile, Path):
                shutil.rmtree(profile, ignore_errors=True)
