"""Explicit, pinned ACP adapter installation in PilferedParrot's state directory.

Importing or locating an adapter never installs packages. The caller invokes
``install()`` after obtaining the operator's install decision. Adapter CLIs are
launched through their package-owned JavaScript entrypoints, never npm shims.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .web_persistence import chat_store_path


MANIFEST_DIR = Path(__file__).resolve().parent.parent / "packaging" / "acp-adapters"
_ACTIVE = "active.json"
_MARKER = "installed.json"
_INSTALL_ID = re.compile(r"[a-z0-9-]{1,80}")


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
    for spec in ADAPTERS.values():
        item = packages.get(f"node_modules/{spec.package}")
        if not isinstance(item, dict) or item.get("version") != spec.version \
                or item.get("integrity") != spec.integrity \
                or not isinstance(item.get("bin"), dict) \
                or item["bin"].get(spec.binary) != "dist/index.js":
            raise AdapterInstallError(f"ACP adapter lock is invalid for {spec.package}")
        if not str(item.get("resolved") or "").startswith("https://registry.npmjs.org/"):
            raise AdapterInstallError(f"ACP adapter registry is invalid for {spec.package}")
    return _sha256(lock_path)


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
        if self.root.is_symlink() or (self.root / "installs").is_symlink():
            raise AdapterInstallError("ACP adapter state must not use symlinked install roots")
        pointer = self.root / _ACTIVE
        if pointer.is_symlink():
            raise AdapterInstallError("ACP adapter pointer must not be a symlink")
        if not pointer.exists():
            return None
        install_id = _read_object(pointer).get("install_id")
        if not isinstance(install_id, str) or not _INSTALL_ID.fullmatch(install_id):
            raise AdapterInstallError("ACP adapter pointer is invalid")
        install = self.root / "installs" / install_id
        if install.is_symlink() or not install.is_dir():
            raise AdapterInstallError("active ACP adapter installation is missing")
        return install

    def _verify(self, install: Path, lock_hash: str) -> dict[str, Path]:
        if (install / _MARKER).is_symlink():
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
        if self.root.is_symlink() or (self.root / "installs").is_symlink():
            raise AdapterInstallError("ACP adapter state must not use symlinked install roots")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "installs").mkdir(mode=0o700, exist_ok=True)
        lock_file = self.root / ".install.lock"
        try:
            descriptor = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            raise AdapterInstallError("an ACP adapter install is already in progress") from error
        stage: Path | None = None
        cache: Path | None = None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
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
            finally:
                pointer_temp.unlink(missing_ok=True)
            return {"installed": True, "path": str(destination)}
        finally:
            if stage is not None and stage.exists():
                shutil.rmtree(stage)
            if cache is not None and cache.exists():
                shutil.rmtree(cache)
            lock_file.unlink(missing_ok=True)
