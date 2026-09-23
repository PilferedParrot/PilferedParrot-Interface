"""Explicit, pinned ACP adapter installation in PilferedParrot's state directory.

Importing or locating an adapter never installs packages. The caller invokes
``install()`` after obtaining the operator's install decision. Adapter CLIs are
launched through their package-owned JavaScript entrypoints, never npm shims.
"""

from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .web_persistence import chat_store_path


MANIFEST_DIR = Path(__file__).resolve().parent.parent / "packaging" / "acp-adapters"
_ACTIVE = "active.json"
_MARKER = "installed.json"
_INSTALL_ID = re.compile(r"[a-z0-9-]{1,80}")
_LOCK_SHA256 = "42161dce0e6ad0a9bee2e1b99ad8c486547efcc724eb52c7d4e9aae28abb1cf2"


@dataclass(frozen=True)
class AdapterSpec:
    package: str
    version: str
    binary: str
    integrity: str


ADAPTERS = {
    "codex": AdapterSpec(
        "@agentclientprotocol/codex-acp", "1.13.1", "codex-acp",
        "sha512-NAbXTb6GRReox7B+8RN9VRB+sKUqFJh5Vg7Ex7RskYCO8EsYPJKN1WJvN7mOqjCKKK9lVDrNxtP7Bp/OUKPyAg==",
    ),
    "claude": AdapterSpec(
        "@agentclientprotocol/claude-agent-acp", "0.81.1", "claude-agent-acp",
        "sha512-I+7tUPsrYnI0nBmdUonoRmdCi7ohyzZ0SeCpeIUFuVZ7a8ZxDyUNO6zBJpaeAIwuPXCk8aw+7t+QiwXS6FwskQ==",
    ),
}


class AdapterInstallError(RuntimeError):
    """A pinned adapter is missing, invalid, or failed to install."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_command(explicit: str | None) -> str:
    node = explicit or shutil.which("node.exe" if sys.platform == "win32" else "node")
    if not node:
        raise AdapterInstallError("Node.js 22 or newer is required for ACP adapters")
    return node


def _npm_command(explicit: str | None) -> str:
    npm = explicit or shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not npm:
        raise AdapterInstallError("npm is required to install ACP adapters")
    return npm


def _npm_argv(explicit: str | None, node: str) -> list[str]:
    npm = _npm_command(explicit)
    path = Path(npm)
    if path.suffix.lower() == ".js":
        return [node, npm]
    if sys.platform != "win32" or path.suffix.lower() not in {".cmd", ".bat"}:
        return [npm]
    # Windows npm shims are batch files. Resolve npm's own JS entrypoint so a
    # state path containing spaces or command characters is never shell-parsed.
    package = path.parent / "node_modules" / "npm"
    manifest = package / "package.json"
    entry = package / "bin" / "npm-cli.js"
    try:
        info = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterInstallError("npm.cmd has no adjacent npm JavaScript package") from error
    if not isinstance(info, dict) or info.get("name") != "npm" \
            or not entry.is_file() or entry.is_symlink():
        raise AdapterInstallError("npm.cmd has no trusted adjacent JavaScript entrypoint")
    return [node, str(entry)]


def _checked_run(command: list[str], *, cwd: Path | None = None,
                 timeout: float = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AdapterInstallError(f"ACP adapter command failed: {type(error).__name__}") from error


def _check_node(node: str) -> None:
    result = _checked_run([node, "--version"], timeout=10)
    match = re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", result.stdout)
    if result.returncode != 0 or match is None or int(match[1]) < 22:
        raise AdapterInstallError("Node.js 22 or newer is required for ACP adapters")


def _locked_manifest() -> str:
    """Validate the checked-in lock against package identity and registry SRI."""
    manifest = json.loads((MANIFEST_DIR / "package.json").read_text(encoding="utf-8"))
    lock_path = MANIFEST_DIR / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected = {spec.package: spec.version for spec in ADAPTERS.values()}
    if manifest.get("private") is not True or manifest.get("dependencies") != expected \
            or lock.get("lockfileVersion") != 3 \
            or lock.get("packages", {}).get("", {}).get("dependencies") != expected:
        raise AdapterInstallError("ACP adapter manifest and lock disagree with pinned packages")
    packages = lock["packages"]
    if _sha256(lock_path) != _LOCK_SHA256:
        raise AdapterInstallError("ACP adapter lockfile differs from the reviewed lock")
    for name, item in packages.items():
        if not name:
            continue
        if not isinstance(item, dict):
            raise AdapterInstallError("ACP adapter lock contains an invalid package entry")
        resolved = item.get("resolved")
        integrity = item.get("integrity")
        if not isinstance(resolved, str) or not resolved.startswith("https://registry.npmjs.org/") \
                or not re.fullmatch(r"https://registry\.npmjs\.org/[^\s?#]+", resolved):
            raise AdapterInstallError(f"ACP adapter registry is invalid for {name}")
        if not isinstance(integrity, str) or not re.fullmatch(r"sha512-[A-Za-z0-9+/]{86}==", integrity):
            raise AdapterInstallError(f"ACP adapter integrity is invalid for {name}")
        try:
            if len(base64.b64decode(integrity[7:], validate=True)) != 64:
                raise ValueError
        except ValueError as error:
            raise AdapterInstallError(f"ACP adapter integrity is invalid for {name}") from error
    for spec in ADAPTERS.values():
        item = packages.get(f"node_modules/{spec.package}")
        if not isinstance(item, dict) or item.get("version") != spec.version \
                or item.get("integrity") != spec.integrity \
                or not isinstance(item.get("bin"), dict) \
                or item["bin"].get(spec.binary) != "dist/index.js":
            raise AdapterInstallError(f"ACP adapter lock is invalid for {spec.package}")
    return _sha256(lock_path)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            return True
        attributes = path.lstat().st_file_attributes
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, FileNotFoundError):
        return False


def _validate_state_paths(root: Path) -> None:
    installs = root / "installs"
    if _is_link_or_reparse(root) or _is_link_or_reparse(installs):
        raise AdapterInstallError("ACP adapter state must not use symlinked or reparse install roots")
    if root.exists() and installs.exists():
        real_root, real_installs = root.resolve(), installs.resolve()
        try:
            real_installs.relative_to(real_root)
        except ValueError as error:
            raise AdapterInstallError("ACP adapter installs escape the state directory") from error


@contextmanager
def _install_lock(path: Path):
    """Acquire an OS lock; the kernel releases it after crashes or process death."""
    if _is_link_or_reparse(path):
        raise AdapterInstallError("ACP adapter install lock must not be a link")
    handle = path.open("a+b")
    if os.name == "posix":
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise AdapterInstallError("an ACP adapter install is already in progress") from error
    elif os.name == "nt":
        import msvcrt
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            handle.close()
            raise AdapterInstallError("an ACP adapter install is already in progress") from error
    else:
        handle.close()
        raise AdapterInstallError("ACP adapter install locking is unsupported on this platform")
    try:
        if os.name == "posix":
            os.fchmod(handle.fileno(), 0o600)
        yield
    finally:
        if os.name == "posix":
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def _cleanup_stale_temps(root: Path, *, older_than: float = 24 * 60 * 60) -> None:
    cutoff = time.time() - older_than
    for path in root.iterdir():
        if not path.name.startswith((".stage-", ".npm-cache-")) or _is_link_or_reparse(path):
            continue
        try:
            path.resolve().relative_to(root.resolve())
            if path.stat().st_mtime < cutoff and path.is_dir():
                shutil.rmtree(path)
        except (OSError, ValueError):
            continue


def _entry(install: Path, spec: AdapterSpec) -> Path:
    package = install / "node_modules" / Path(spec.package)
    if package.is_symlink() or not package.is_dir():
        raise AdapterInstallError(f"{spec.package} is missing")
    try:
        info = json.loads((package / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterInstallError(f"{spec.package} has no valid package manifest") from error
    if info.get("name") != spec.package or info.get("version") != spec.version:
        raise AdapterInstallError(f"{spec.package} has the wrong version")
    bins = info.get("bin")
    relative = bins.get(spec.binary) if isinstance(bins, dict) else bins
    if not isinstance(relative, str) or not relative:
        raise AdapterInstallError(f"{spec.package} has no supported executable")
    package_root = package.resolve()
    try:
        package_root.relative_to(install.resolve())
    except ValueError as error:
        raise AdapterInstallError(f"{spec.package} escapes the installation") from error
    entry = (package_root / relative).resolve()
    try:
        entry.relative_to(package_root)
    except ValueError as error:
        raise AdapterInstallError(f"{spec.package} executable escapes its package") from error
    if not entry.is_file():
        raise AdapterInstallError(f"{spec.package} executable is missing")
    return entry


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterInstallError(f"invalid ACP adapter metadata: {path.name}") from error
    if not isinstance(value, dict):
        raise AdapterInstallError(f"invalid ACP adapter metadata: {path.name}")
    return value


class AdapterManager:
    """Locate or explicitly install the pinned adapters for one PPI config."""

    def __init__(self, config: dict[str, Any], *, node: str | None = None,
                 npm: str | None = None) -> None:
        self.root = chat_store_path(config).parent / "acp-adapters"
        self._node = node
        self._npm = npm

    def _active_install(self) -> Path | None:
        _validate_state_paths(self.root)
        pointer = self.root / _ACTIVE
        if _is_link_or_reparse(pointer):
            raise AdapterInstallError("ACP adapter pointer must not be a symlink")
        if not pointer.exists():
            return None
        install_id = _read_object(pointer).get("install_id")
        if not isinstance(install_id, str) or not _INSTALL_ID.fullmatch(install_id):
            raise AdapterInstallError("ACP adapter pointer is invalid")
        install = self.root / "installs" / install_id
        _validate_state_paths(self.root)
        if _is_link_or_reparse(install) or not install.is_dir():
            raise AdapterInstallError("active ACP adapter installation is missing")
        try:
            install.resolve().relative_to((self.root / "installs").resolve())
        except ValueError as error:
            raise AdapterInstallError("active ACP adapter installation escapes its root") from error
        return install

    def _verify(self, install: Path, lock_hash: str) -> dict[str, Path]:
        if _is_link_or_reparse(install / _MARKER):
            raise AdapterInstallError("ACP adapter metadata must not be a symlink")
        marker = _read_object(install / _MARKER)
        if marker.get("lock_sha256") != lock_hash:
            raise AdapterInstallError("ACP adapter installation uses a different lock")
        recorded = marker.get("entries")
        if not isinstance(recorded, dict):
            raise AdapterInstallError("ACP adapter installation has invalid entry metadata")
        entries: dict[str, Path] = {}
        for provider, spec in ADAPTERS.items():
            entry = _entry(install, spec)
            expected = recorded.get(provider)
            if not isinstance(expected, dict) or expected.get("path") != str(entry.relative_to(install)) \
                    or expected.get("sha256") != _sha256(entry):
                raise AdapterInstallError(f"ACP adapter executable changed: {spec.package}")
            entries[provider] = entry
        return entries

    def locate(self, provider: str) -> list[str] | None:
        """Return ``[node, installed JS entry]``; never download or mutate."""
        if provider not in ADAPTERS:
            raise ValueError("unsupported ACP adapter provider")
        install = self._active_install()
        if install is None:
            return None
        entries = self._verify(install, _locked_manifest())
        node = _node_command(self._node)
        _check_node(node)
        return [node, str(entries[provider])]

    def install(self) -> dict[str, Any]:
        """Install after an explicit caller action; retain every prior install."""
        lock_hash = _locked_manifest()
        node = _node_command(self._node)
        _check_node(node)
        existing = self._active_install()
        if existing is not None:
            try:
                self._verify(existing, lock_hash)
            except AdapterInstallError:
                pass  # Preserve it; a fresh versioned install becomes active.
            else:
                return {"installed": False, "path": str(existing)}
        npm_argv = _npm_argv(self._npm, node)
        _validate_state_paths(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "installs").mkdir(mode=0o700, exist_ok=True)
        _validate_state_paths(self.root)
        lock_file = self.root / ".install.lock"
        with _install_lock(lock_file):
            _cleanup_stale_temps(self.root)
            stage: Path | None = None
            cache: Path | None = None
            destination: Path | None = None
            activated = False
            try:
                stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.root))
                cache = Path(tempfile.mkdtemp(prefix=".npm-cache-", dir=self.root))
                for name in ("package.json", "package-lock.json"):
                    shutil.copy2(MANIFEST_DIR / name, stage / name)
                result = _checked_run([
                    *npm_argv, "ci", "--ignore-scripts", "--no-audit", "--no-fund",
                    "--cache", str(cache),
                ], cwd=stage, timeout=300)
                if result.returncode != 0:
                    raise AdapterInstallError(f"npm ci failed with exit {result.returncode}")
                marker = {"lock_sha256": lock_hash, "entries": {}}
                for provider, spec in ADAPTERS.items():
                    entry = _entry(stage, spec)
                    checked = _checked_run([node, "--check", str(entry)], timeout=15)
                    if checked.returncode != 0:
                        raise AdapterInstallError(f"{spec.package} failed Node syntax verification")
                    marker["entries"][provider] = {
                        "path": str(entry.relative_to(stage)), "sha256": _sha256(entry),
                    }
                marker_path = stage / _MARKER
                marker_path.write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")
                if os.name == "posix":
                    marker_path.chmod(0o600)
                install_id = uuid.uuid4().hex
                destination = self.root / "installs" / install_id
                os.replace(stage, destination)
                stage = None
                pointer_temp = self.root / f".{_ACTIVE}.{uuid.uuid4().hex}.tmp"
                try:
                    pointer_temp.write_text(json.dumps({"install_id": install_id}) + "\n",
                                            encoding="utf-8")
                    if os.name == "posix":
                        pointer_temp.chmod(0o600)
                    os.replace(pointer_temp, self.root / _ACTIVE)
                    activated = True
                finally:
                    pointer_temp.unlink(missing_ok=True)
                return {"installed": True, "path": str(destination)}
            finally:
                if stage is not None and stage.exists():
                    shutil.rmtree(stage)
                if not activated and destination is not None and destination.exists():
                    shutil.rmtree(destination)
                if cache is not None and cache.exists():
                    shutil.rmtree(cache)
