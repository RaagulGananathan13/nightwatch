"""A small Model Context Protocol server over stdio (spec F15).

An MCP client (Claude Code, Claude Desktop or any other) starts this as a subprocess and talks
JSON-RPC 2.0 over its stdin and stdout, one message per line. A server that only offers tools
needs four methods: initialize, ping, tools/list and tools/call. They are written out here
instead of pulling in a framework, so the whole surface fits in one short file.

Nothing but MCP messages may go to stdout; ``__main__`` sends everything else to stderr.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import IO, Any

# Newest first. A client asking for one of these gets it; any other request gets the newest.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

Call = Callable[[str, dict[str, Any]], tuple[str, bool]]  # name, arguments -> text, failed


class Server:
    def __init__(
        self, info: dict[str, str], instructions: str, tools: list[dict[str, Any]], call: Call
    ) -> None:
        self.info = info
        self.instructions = instructions
        self.tools = tools
        self.call = call
        self._names = {tool["name"] for tool in tools}

    def serve(self, stdin: IO[str], stdout: IO[str]) -> None:
        """Answer messages until the client closes stdin."""
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                reply: Any = _error(None, PARSE_ERROR, "that line isn't JSON")
            else:
                reply = self.handle(message)
            if reply is not None:
                stdout.write(json.dumps(reply) + "\n")  # ASCII-only JSON: one line per message
                stdout.flush()

    def handle(self, message: Any) -> Any:
        """One message (or a batch) in, its response out; None when there's nothing to say."""
        if isinstance(message, list):
            replies = [reply for reply in map(self.handle, message) if reply is not None]
            return replies or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in message or "id" not in message:
            return None  # a notification (initialized, cancelled) or a response: no reply
        ident, method = message["id"], message["method"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(ident, INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return _result(ident, self._initialize(params))
        if method == "ping":
            return _result(ident, {})
        if method == "tools/list":
            return _result(ident, {"tools": self.tools})
        if method == "tools/call":
            return self._call(ident, params)
        return _error(ident, METHOD_NOT_FOUND, f"NightWatch doesn't offer {str(method)[:60]}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": self.info,
            "instructions": self.instructions,
        }

    def _call(self, ident: Any, params: dict[str, Any]) -> dict[str, Any]:
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name not in self._names:
            return _error(ident, INVALID_PARAMS, f"unknown tool: {str(name)[:60]}")
        if not isinstance(arguments, dict):
            return _error(ident, INVALID_PARAMS, "arguments must be an object")
        try:
            text, failed = self.call(str(name), arguments)
        except Exception as exc:  # one bad call mustn't end the session
            sys.stderr.write(f"NightWatch MCP: {name} failed: {exc!r}\n")
            text, failed = f"NightWatch failed: {type(exc).__name__}", True
        return _result(ident, {"content": [{"type": "text", "text": text}], "isError": failed})


def _result(ident: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}
