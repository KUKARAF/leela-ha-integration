"""Minimal MCP (streamable HTTP, JSON-RPC 2.0) server logic, HA-independent."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
import hmac
import time
from typing import Any

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "leela-button-presser", "version": "0.1.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
RATE_LIMITED = -32029


def check_bearer(header: str | None, secret: str) -> bool:
    """Constant-time check of an 'Authorization: Bearer <secret>' header."""
    if not secret or not header:
        return False
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(token.strip().encode(), secret.encode())


class RateLimiter:
    """Sliding one-minute window."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: deque[float] = deque()

    def allow(self, limit: int) -> bool:
        now = self._clock()
        while self._hits and now - self._hits[0] >= 60:
            self._hits.popleft()
        if len(self._hits) >= limit:
            return False
        self._hits.append(now)
        return True


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def tool_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# list_tools() -> tool descriptors ; call_tool(name, args) -> result dict or
# None if the tool is unknown.
ListTools = Callable[[], list[dict[str, Any]]]
CallTool = Callable[[str, Any], Awaitable[dict[str, Any] | None]]


async def handle_message(
    msg: Any, list_tools: ListTools, call_tool: CallTool
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. None means 'no response body' (notification)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, INVALID_REQUEST, "invalid request")
    method = msg.get("method")
    if not isinstance(method, str):
        return _err(msg.get("id"), INVALID_REQUEST, "invalid request")
    if "id" not in msg:  # notification
        return None
    rid = msg["id"]
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _err(rid, INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _ok(
            rid,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": list_tools()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _err(rid, INVALID_PARAMS, "missing tool name")
        result = await call_tool(name, params.get("arguments"))
        if result is None:
            return _err(rid, INVALID_PARAMS, f"unknown tool: {name[:64]}")
        return _ok(rid, result)
    return _err(rid, METHOD_NOT_FOUND, "method not found")
