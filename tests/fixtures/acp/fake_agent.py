"""Deterministic ACP v1 stdio agent for transport tests, not a product adapter."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


root = Path.cwd()
record = root / "fake-agent-requests.jsonl"
sent_record = root / "fake-agent-sent.jsonl"
session_file = root / "fake-agent-session.json"
pending_prompt = None
pending_permission = None
pending_content = None
session_id = "fake-session"
sentinel = os.environ.get("FAKE_ACP_SECRET", "private.person@example.test")


def send(value):
    with sent_record.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **value}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(message, value):
    send({"id": message["id"], "result": value})


def update(value):
    send({"method": "session/update", "params": {"sessionId": session_id, "update": value}})


def options(model="luna"):
    choices = ([{"value": "fake-small", "name": "Fake Small"},
                {"value": "fake-large", "name": "Fake Large"}]
               if os.environ.get("FAKE_ACP_BROWSER_E2E") == "1" else
               [{"value": "luna", "name": "Luna"}, {"value": "sol", "name": "Sol"}])
    if os.environ.get("FAKE_ACP_BROWSER_E2E") == "1" and model == "luna":
        model = "fake-small"
    return [{"id": "model", "name": "Model", "category": "model",
             "type": "select", "currentValue": model,
             "options": choices}]


print(f"fake agent boot {sentinel}", file=sys.stderr, flush=True)
for raw in sys.stdin:
    message = json.loads(raw)
    with record.open("a", encoding="utf-8") as handle:
        handle.write(raw)
    method = message.get("method")
    if method == "initialize":
        result(message, {"protocolVersion": 1,
                         "agentCapabilities": {"loadSession": True,
                                               "sessionCapabilities": {"resume": {}, "close": {}}}})
    elif method == "session/new":
        session_file.write_text(json.dumps({"sessionId": session_id, "mode": "default"}), encoding="utf-8")
        current_mode = "default"
        result(message, {"sessionId": session_id, "configOptions": options(),
                         "modes": {"currentModeId": current_mode, "availableModes": [
                             {"id": "default", "name": "Agent default"},
                             {"id": "plan", "name": "Plan", "description": "Plan before acting"},
                         ]}})
        if os.environ.get("FAKE_ACP_PAUSE_AFTER_NEW") == "1":
            time.sleep(60)
    elif method in {"session/load", "session/resume"}:
        if session_file.exists() and message["params"]["sessionId"] == session_id:
            saved_session = json.loads(session_file.read_text(encoding="utf-8"))
            result(message, {"configOptions": options(), "modes": {
                "currentModeId": saved_session.get("mode", "default"),
                "availableModes": [{"id": "default", "name": "Agent default"},
                                   {"id": "plan", "name": "Plan"}],
            }})
        else:
            send({"id": message["id"], "error": {"code": -32000, "message": "unknown session"}})
    elif method == "session/close":
        result(message, {})
    elif method == "session/set_config_option":
        model = message["params"]["value"]
        update({"sessionUpdate": "config_option_update", "configOptions": options(model)})
        result(message, {"configOptions": options(model)})
    elif method == "session/set_mode":
        saved_session = json.loads(session_file.read_text(encoding="utf-8"))
        saved_session["mode"] = message["params"]["modeId"]
        session_file.write_text(json.dumps(saved_session), encoding="utf-8")
        with record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"fixture": "set_mode", "modeId": message["params"]["modeId"]}) + "\n")
        update({"sessionUpdate": "current_mode_update", "currentModeId": message["params"]["modeId"]})
        result(message, {"currentModeId": message["params"]["modeId"]})
    elif method == "session/prompt":
        with record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"fixture": "prompt"}) + "\n")
        content = message["params"]["prompt"][0]["text"]
        if content == "exit":
            sys.exit(0)
        if content == "error":
            send({"id": message["id"], "error": {"code": -32000,
                  "message": f"agent failed for {sentinel}", "accountEmail": sentinel}})
            continue
        if content == "invalid-json":
            sys.stdout.write("{invalid-json}\n")
            sys.stdout.flush()
            time.sleep(60)
            continue
        if content == "oversized-line":
            sys.stdout.write("x" * 5000 + "\n")
            sys.stdout.flush()
            time.sleep(60)
            continue
        pending_prompt = message
        pending_content = content
        update({"sessionUpdate": "_auth/status_update", "accountEmail": sentinel})
        send({"method": "_auth/status_update", "params": {"email": sentinel}})
        if content == "browser-e2e-split":
            split_at = len(sentinel) // 2
            for part in (sentinel[:split_at], sentinel[split_at:]):
                update({"sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": part},
                        "account": {"email": sentinel}})
        else:
            update({"sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": f"working on {content}"},
                    "account": {"email": sentinel}})
        if content in {"wait-cancel", "ignore-cancel"}:
            continue
        browser_e2e = content.startswith("browser-e2e-")
        if browser_e2e:
            update({"sessionUpdate": "tool_call_update", "toolCallId": "browser-tool-1",
                    "title": "Prepare browser preview", "kind": "edit", "status": "completed",
                    "content": [{"type": "diff", "path": "preview.txt", "oldText": None,
                                 "newText": "ACP browser preview\n"}]})
        pending_permission = "permission-1"
        permission = {"sessionId": session_id,
                      "toolCall": {"toolCallId": "tool-1", "title": "Write allowed.txt",
                                   "account": {"email": sentinel},
                                   "_meta": {"accountEmail": sentinel}},
                      "options": [
                          {"optionId": "yes", "name": "Allow once", "kind": "allow_once",
                           "_meta": {"accountEmail": sentinel}},
                          {"optionId": "no", "name": "Reject once", "kind": "reject_once"},
                      ], "accountEmail": sentinel}
        if browser_e2e:
            permission["toolCall"].update({
                "name": "shell", "kind": "execute",
                "rawInput": {"command": "printf 'approved\\n' > allowed.txt"},
                "content": [{"type": "diff", "path": "allowed.txt", "oldText": None,
                             "newText": "approved\n"}],
            })
        if content == "malformed-permission":
            permission.pop("sessionId")
        elif content == "malformed-session-type":
            permission["sessionId"] = 42
        elif content == "malformed-tool-call":
            permission.pop("toolCall")
        elif content == "malformed-tool-id":
            permission["toolCall"].pop("toolCallId")
        elif content == "malformed-options":
            permission.pop("options")
        elif content == "malformed-option-item":
            permission["options"][0].pop("kind")
        send({"id": pending_permission, "method": "session/request_permission",
              "params": permission})
    elif method == "session/cancel":
        if pending_prompt is not None and pending_content != "ignore-cancel":
            result(pending_prompt, {"stopReason": "cancelled"})
            pending_prompt = None
    elif pending_permission is not None and message.get("id") == pending_permission:
        choice = (message.get("result") or {}).get("outcome", {})
        if choice.get("outcome") == "selected" and choice.get("optionId") == "yes":
            (root / "allowed.txt").write_text("changed", encoding="utf-8")
        if pending_prompt is not None:
            result(pending_prompt, {"stopReason": "end_turn"})
        pending_prompt = None
        pending_permission = None
    elif "id" in message:
        send({"id": message["id"], "error": {"code": -32601, "message": "not implemented"}})
