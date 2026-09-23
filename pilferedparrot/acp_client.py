"""ACP v1 JSON-RPC/stdio transport. No browser or provider policy lives here.

The caller selects the agent executable and decides permissions. Inbound auth
status and account identifiers are removed before any callback or error surface.
"""

from __future__ import annotations

import json
import queue
import re
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, InvalidStateError
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from . import __version__


class ACPError(RuntimeError):
    """The agent, protocol, or transport failed."""


class ACPClosed(ACPError):
    """The agent process exited or the client closed."""


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PRIVATE_KEYS = frozenset({
    "account", "accountid", "accountemail", "email", "apikey", "accesstoken",
    "refreshtoken", "authorization", "credential", "credentials", "secret", "token",
})
_AUTH_UPDATE = "_auth/status_update"
_PERMISSION_KINDS = frozenset({"allow_once", "allow_always", "reject_once", "reject_always"})


def _safe(value: Any) -> Any:
    """Remove known account fields recursively before data leaves ingress."""
    if isinstance(value, dict):
        return {
            key: _safe(item) for key, item in value.items()
            if isinstance(key, str) and re.sub(r"[^a-z0-9]", "", key.lower()) not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return _EMAIL.sub("[redacted-email]", value)
    return value


def _valid_permission_request(params: dict[str, Any]) -> bool:
    """Check the required ACP v1 shape before a user decision is requested."""
    session_id = params.get("sessionId")
    tool_call = params.get("toolCall")
    options = params.get("options")
    if not isinstance(session_id, str) or not session_id \
            or not isinstance(tool_call, dict) \
            or not isinstance(tool_call.get("toolCallId"), str) \
            or not tool_call["toolCallId"] \
            or not isinstance(options, list):
        return False
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, dict) \
                or not isinstance(option.get("optionId"), str) or not option["optionId"] \
                or not isinstance(option.get("name"), str) \
                or not isinstance(option.get("kind"), str) \
                or option["kind"] not in _PERMISSION_KINDS \
                or option["optionId"] in seen:
            return False
        seen.add(option["optionId"])
    return True


class ACPClient:
    """One agent process with concurrent requests and ordered update callbacks.

    ``on_update(session_id, update)`` runs on one dispatcher thread, in wire
    order. If it fails, the client fails pending work and stops the agent.
    ``on_permission(params)`` returns an
    optionId from the offered options; without it, the client rejects. A
    callback error also rejects. Client fs/terminal methods are not advertised
    or served. One bounded writer queue keeps pipe backpressure inside request
    timeouts; ``close()`` stops both the agent and transport threads.
    """

    def __init__(
        self, argv: Sequence[str], *, cwd: Path,
        on_update: Callable[[str, dict[str, Any]], None] | None = None,
        on_permission: Callable[[dict[str, Any]], str | None] | None = None,
        env: dict[str, str] | None = None,
        max_line_chars: int = 4 * 1024 * 1024,
        stderr_limit: int = 16 * 1024,
    ) -> None:
        if not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError("agent argv must be a non-empty sequence of text arguments")
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("agent cwd must be an existing absolute directory")
        self._proc = subprocess.Popen(
            list(argv), cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self._state_lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._pending: dict[int, Future[Any]] = {}
        self._pending_methods: dict[int, str] = {}
        self._permissions: dict[int | str, str] = {}
        self._next_id = 0
        self._failure: ACPClosed | None = None
        self._closed = False
        self._stderr_raw_tail = ""
        self._stderr_tail = ""
        self._stderr_limit = max(0, stderr_limit)
        self._max_line_chars = max(1024, max_line_chars)
        self._on_update = on_update
        self._on_permission = on_permission
        self._capabilities: dict[str, Any] = {}
        self._initialized = False
        self._outbox: queue.Queue[str] = queue.Queue(maxsize=256)
        self._writer_stop = threading.Event()
        self._dispatch_stop = threading.Event()
        self._updates: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=2048)
        self._permission_slots = threading.BoundedSemaphore(16)
        self._writer = threading.Thread(target=self._write_stdin, daemon=True,
                                        name="ppi-acp-writer")
        self._reader = threading.Thread(target=self._read_stdout, daemon=True, name="ppi-acp-reader")
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True,
                                               name="ppi-acp-stderr")
        self._dispatcher = threading.Thread(target=self._dispatch_updates, daemon=True,
                                            name="ppi-acp-updates")
        self._writer.start()
        self._reader.start()
        self._stderr_reader.start()
        self._dispatcher.start()

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def agent_capabilities(self) -> dict[str, Any]:
        return dict(self._capabilities)

    @property
    def stderr_tail(self) -> str:
        with self._stderr_lock:
            return self._stderr_tail

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for chunk in iter(lambda: self._proc.stderr.read(4096), ""):
            with self._stderr_lock:
                # Keep overlap across reads so an address split at a chunk
                # boundary is redacted before the public tail is truncated.
                self._stderr_raw_tail = (self._stderr_raw_tail + chunk)[
                    -(self._stderr_limit + 1024):
                ]
                self._stderr_tail = str(_safe(self._stderr_raw_tail))[-self._stderr_limit:] \
                    if self._stderr_limit else ""

    def _fail(self, detail: str) -> None:
        with self._state_lock:
            if self._failure is None:
                self._failure = ACPClosed(detail)
            pending = list(self._pending.values())
            self._pending.clear()
            self._pending_methods.clear()
        for future in pending:
            if not future.done():
                try:
                    future.set_exception(self._failure)
                except InvalidStateError:
                    pass

    def _send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        if len(line) > self._max_line_chars:
            raise ValueError("ACP outbound JSON-RPC line exceeds limit")
        with self._state_lock:
            if self._closed or self._failure is not None:
                raise self._failure or ACPClosed("ACP client is closed")
        try:
            self._outbox.put_nowait(line)
        except queue.Full as error:
            self._fail("ACP outbound queue is full")
            self._stop_process()
            raise ACPClosed("ACP outbound queue is full") from error

    def _write_stdin(self) -> None:
        assert self._proc.stdin is not None
        while not self._writer_stop.is_set():
            try:
                line = self._outbox.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                with self._state_lock:
                    if self._failure is not None or self._closed:
                        return
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except Exception as error:
                self._fail(f"ACP agent stdin failed: {type(error).__name__}")
                self._stop_process()
                return
            finally:
                self._outbox.task_done()

    def _start_request(self, method: str, params: dict[str, Any]) -> tuple[int, Future[Any]]:
        with self._state_lock:
            if self._closed or self._failure is not None:
                raise self._failure or ACPClosed("ACP client is closed")
            self._next_id += 1
            request_id = self._next_id
            future: Future[Any] = Future()
            self._pending[request_id] = future
            self._pending_methods[request_id] = method
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        except Exception:
            self._forget_request(request_id)
            raise
        return request_id, future

    def _forget_request(self, request_id: int) -> None:
        with self._state_lock:
            self._pending.pop(request_id, None)
            self._pending_methods.pop(request_id, None)

    def request(self, method: str, params: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        request_id, future = self._start_request(method, params)
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout as error:
            raise TimeoutError(f"ACP {method} timed out") from error
        finally:
            self._forget_request(request_id)
        if not isinstance(result, dict):
            raise ACPError(f"ACP {method} returned a non-object result")
        return result

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        try:
            while True:
                line = self._proc.stdout.readline(self._max_line_chars + 1)
                if not line:
                    self._fail("ACP agent stdout closed")
                    return
                if len(line) > self._max_line_chars or not line.endswith("\n"):
                    self._fail("ACP agent sent an oversized or incomplete JSON-RPC line")
                    self._stop_process()
                    return
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._fail("ACP agent sent invalid JSON-RPC")
                    self._stop_process()
                    return
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    self._fail("ACP agent sent invalid JSON-RPC")
                    self._stop_process()
                    return
                self._ingest(message)
        except Exception as error:
            # Never include the exception text: an agent may embed account data
            # in malformed payloads or callback-path errors.
            self._fail(f"ACP ingress failed: {type(error).__name__}")
            self._stop_process()

    def _ingest(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str):
            if method == _AUTH_UPDATE:
                return
            params = message.get("params")
            if not isinstance(params, dict):
                params = {}
            if method == "session/update":
                update = params.get("update")
                if not isinstance(update, dict) or update.get("sessionUpdate") == _AUTH_UPDATE:
                    return
                session_id = params.get("sessionId")
                if isinstance(session_id, str) and self._on_update is not None:
                    try:
                        self._updates.put(("update", str(_safe(session_id)), _safe(update)),
                                          timeout=1)
                    except queue.Full:
                        self._fail("ACP update consumer fell behind")
                        self._stop_process()
                return
            if "id" in message:
                request_id = message["id"]
                if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
                    self._fail("ACP agent sent an invalid request id")
                    return
                if method == "session/request_permission":
                    if not _valid_permission_request(params):
                        self._send({"jsonrpc": "2.0", "id": request_id,
                                    "result": {"outcome": {"outcome": "cancelled"}}})
                        return
                    session_id = params["sessionId"]
                    with self._state_lock:
                        self._permissions[request_id] = session_id
                    if self._permission_slots.acquire(blocking=False):
                        threading.Thread(target=self._answer_permission,
                                         args=(request_id, _safe(params)), daemon=True,
                                         name="ppi-acp-permission").start()
                    else:
                        with self._state_lock:
                            self._permissions.pop(request_id, None)
                        self._send({"jsonrpc": "2.0", "id": request_id,
                                    "result": {"outcome": {"outcome": "cancelled"}}})
                else:
                    self._send({"jsonrpc": "2.0", "id": request_id,
                                "error": {"code": -32601, "message": "unsupported client method"}})
            return
        request_id = message.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        with self._state_lock:
            future = self._pending.get(request_id)
            pending_method = self._pending_methods.get(request_id)
        if future is None or future.done():
            return
        safe_message = _safe(message)
        if pending_method == "session/prompt" and self._on_update is not None:
            try:
                self._updates.put(("complete", future, safe_message), timeout=1)
            except queue.Full:
                self._fail("ACP update consumer fell behind")
                self._stop_process()
            return
        self._finish_future(future, safe_message)

    @staticmethod
    def _finish_future(future: Future[Any], message: dict[str, Any]) -> None:
        if future.done():
            return
        if "error" in message:
            try:
                future.set_exception(ACPError(f"ACP request failed: {message['error']}"))
            except InvalidStateError:
                pass
        else:
            try:
                future.set_result(message.get("result"))
            except InvalidStateError:
                pass

    def _dispatch_updates(self) -> None:
        while True:
            try:
                item = self._updates.get(timeout=0.1)
            except queue.Empty:
                if self._dispatch_stop.is_set():
                    return
                continue
            try:
                if self._dispatch_stop.is_set():
                    continue
                if item[0] == "complete":
                    self._finish_future(item[1], item[2])
                elif item[0] == "update" and self._on_update is not None:
                    try:
                        self._on_update(item[1], item[2])
                    except Exception as error:
                        self._fail(f"ACP update callback failed: {type(error).__name__}")
                        self._stop_process()
            except Exception as error:
                self._fail(f"ACP update dispatch failed: {type(error).__name__}")
                self._stop_process()
            finally:
                self._updates.task_done()

    def _answer_permission(self, request_id: int | str, params: dict[str, Any]) -> None:
        try:
            options = params.get("options")
            options = options if isinstance(options, list) else []
            offered = {item["optionId"]: item for item in options if isinstance(item, dict)
                       and isinstance(item.get("optionId"), str)}
            choice: str | None = None
            if self._on_permission is not None:
                try:
                    selected = self._on_permission(params)
                    if isinstance(selected, str) and selected in offered:
                        choice = selected
                except Exception:
                    pass
            if choice is None:
                choice = next((item["optionId"] for item in options if isinstance(item, dict)
                               and isinstance(item.get("optionId"), str)
                               and item.get("kind") == "reject_once"), None)
            if choice is None:
                choice = next((item["optionId"] for item in options if isinstance(item, dict)
                               and isinstance(item.get("optionId"), str)
                               and item.get("kind") == "reject_always"), None)
            outcome = {"outcome": "selected", "optionId": choice} if choice is not None \
                else {"outcome": "cancelled"}
            with self._state_lock:
                if request_id not in self._permissions:
                    return
                self._permissions.pop(request_id, None)
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "result": {"outcome": outcome}})
            except ACPClosed:
                pass
        finally:
            self._permission_slots.release()

    def initialize(self, *, timeout: float = 120) -> dict[str, Any]:
        result = self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "PilferedParrot", "version": __version__},
        }, timeout=timeout)
        if result.get("protocolVersion") != 1:
            raise ACPError("ACP agent does not support protocol v1")
        self._capabilities = result.get("agentCapabilities") \
            if isinstance(result.get("agentCapabilities"), dict) else {}
        self._initialized = True
        return result

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ACPError("initialize the ACP agent first")

    @staticmethod
    def _session_params(cwd: Path, session_id: str | None = None) -> dict[str, Any]:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("session cwd must be an existing absolute directory")
        params: dict[str, Any] = {"cwd": str(cwd), "mcpServers": []}
        if session_id is not None:
            params["sessionId"] = session_id
        return params

    def new_session(self, cwd: Path, *, timeout: float = 120) -> dict[str, Any]:
        self._require_initialized()
        result = self.request("session/new", self._session_params(cwd), timeout=timeout)
        if not isinstance(result.get("sessionId"), str) or not result["sessionId"]:
            raise ACPError("ACP session/new omitted sessionId")
        return result

    def load_session(self, session_id: str, cwd: Path, *, timeout: float = 120) -> dict[str, Any]:
        self._require_initialized()
        if self._capabilities.get("loadSession") is not True:
            raise ACPError("ACP agent does not support session/load")
        return self.request("session/load", self._session_params(cwd, session_id), timeout=timeout)

    def resume_session(self, session_id: str, cwd: Path, *, timeout: float = 120) -> dict[str, Any]:
        self._require_initialized()
        sessions = self._capabilities.get("sessionCapabilities") or {}
        if not isinstance(sessions, dict) or "resume" not in sessions:
            raise ACPError("ACP agent does not support session/resume")
        return self.request("session/resume", self._session_params(cwd, session_id), timeout=timeout)

    def close_session(self, session_id: str, *, timeout: float = 30) -> dict[str, Any]:
        self._require_initialized()
        sessions = self._capabilities.get("sessionCapabilities") or {}
        if not isinstance(sessions, dict) or "close" not in sessions:
            raise ACPError("ACP agent does not support session/close")
        return self.request("session/close", {"sessionId": session_id}, timeout=timeout)

    def set_config_option(self, session_id: str, config_id: str, value: str | bool,
                          *, timeout: float = 30) -> dict[str, Any]:
        self._require_initialized()
        if not isinstance(value, (str, bool)):
            raise ValueError("ACP config value must be text or boolean")
        params: dict[str, Any] = {"sessionId": session_id, "configId": config_id, "value": value}
        if isinstance(value, bool):
            params["type"] = "boolean"
        return self.request("session/set_config_option", params, timeout=timeout)

    def set_mode(self, session_id: str, mode_id: str, *, timeout: float = 30) -> dict[str, Any]:
        self._require_initialized()
        return self.request("session/set_mode", {"sessionId": session_id, "modeId": mode_id},
                            timeout=timeout)

    def prompt(self, session_id: str, content: str | list[dict[str, Any]],
               *, timeout: float = 600, cancel_grace: float = 2.0) -> dict[str, Any]:
        self._require_initialized()
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("prompt needs at least one content block")
        if timeout <= 0 or not 0 < cancel_grace <= 30:
            raise ValueError("prompt timeout and cancel grace must be positive and bounded")
        request_id, future = self._start_request(
            "session/prompt", {"sessionId": session_id, "prompt": blocks},
        )
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout as error:
            try:
                self.cancel(session_id)
            except ACPClosed:
                pass
            try:
                future.result(timeout=cancel_grace)
            except FutureTimeout:
                self._fail("ACP agent ignored prompt cancellation")
                self._stop_process()
            except ACPError:
                raise
            raise TimeoutError("ACP session/prompt timed out") from error
        finally:
            self._forget_request(request_id)
        if not isinstance(result, dict):
            raise ACPError("ACP session/prompt returned a non-object result")
        if not isinstance(result.get("stopReason"), str):
            raise ACPError("ACP session/prompt omitted stopReason")
        return result

    def cancel(self, session_id: str) -> None:
        self._require_initialized()
        with self._state_lock:
            waiting = [request_id for request_id, owner in self._permissions.items()
                       if owner == session_id]
            for request_id in waiting:
                self._permissions.pop(request_id, None)
        for request_id in waiting:
            self._send({"jsonrpc": "2.0", "id": request_id,
                        "result": {"outcome": {"outcome": "cancelled"}}})
        self._send({"jsonrpc": "2.0", "method": "session/cancel",
                    "params": {"sessionId": session_id}})

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._fail("ACP client closed")
        self._writer_stop.set()
        self._dispatch_stop.set()
        self._stop_process()
        for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self._writer.join(timeout=2)
        self._reader.join(timeout=2)
        self._stderr_reader.join(timeout=2)
        self._dispatcher.join(timeout=2)

    def _stop_process(self) -> None:
        with self._stop_lock:
            if self._proc.poll() is not None:
                return
            pid = self._proc.pid
            if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
                return
            if sys.platform == "win32":
                try:
                    killed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, timeout=2, check=False)
                    if killed.returncode != 0 and self._proc.poll() is None:
                        self._proc.terminate()
                except (OSError, subprocess.TimeoutExpired):
                    self._proc.terminate()
            else:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    self._proc.kill()
                else:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        self._proc.kill()
                self._proc.wait(timeout=2)

    def __enter__(self) -> ACPClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
