"""HTTP endpoint (HomeAssistantView) and tool execution."""

from __future__ import annotations

from http import HTTPStatus
import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.http import HomeAssistantView

from .const import API_PATH, CONF_SECRET, DOMAIN
from .mcp import (
    PARSE_ERROR,
    RATE_LIMITED,
    RateLimiter,
    check_bearer,
    handle_message,
    tool_result,
)
from .tools import (
    READ_ATTRIBUTES,
    Options,
    ReadCall,
    ServiceCall,
    ToolError,
    build_tools,
    check_call,
    dumps,
    parse_options,
)

_LOGGER = logging.getLogger(__name__)
MAX_BODY = 64 * 1024
MAX_AUTH_FAILURES_PER_MIN = 10


class Runtime:
    """Per-entry state: the limiters and live access to the entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.calls = RateLimiter()
        self.auth_failures = RateLimiter()

    @property
    def secret(self) -> str:
        return str(self.entry.data.get(CONF_SECRET, ""))

    @property
    def options(self) -> Options:
        # Re-read on every request so option changes apply immediately.
        return parse_options(self.entry.options)

    async def call_tool(self, name: str, args: Any) -> dict[str, Any] | None:
        opts = self.options
        tool = build_tools(opts).get(name)
        if tool is None:
            return None
        if not self.calls.allow(opts.rate_limit):
            _LOGGER.warning("rate limit hit, rejecting tool %s", name)
            return tool_result(
                f"rate limit exceeded ({opts.rate_limit} calls/minute)", True
            )
        try:
            plan = tool.plan(args)
            if isinstance(plan, ReadCall):
                out = self._read(opts, plan)
            else:
                out = await self._execute(opts, plan)
        except ToolError as err:
            _LOGGER.info("tool %s rejected: %s", name, err)
            return tool_result(str(err), True)
        except HomeAssistantError as err:
            _LOGGER.warning("tool %s failed: %s", name, err)
            return tool_result(f"Home Assistant error: {err}", True)
        return tool_result(out)

    def _read(self, opts: Options, plan: ReadCall) -> str:
        if plan.entity_id is None:
            return dumps(
                {
                    "controllable": sorted(opts.controllable()),
                    "readable": list(opts.readable()),
                    "rooms": dict.fromkeys(opts.rooms, "ok"),
                }
            )
        if plan.entity_id not in opts.readable():
            raise ToolError("entity not allowed")
        state = self.hass.states.get(plan.entity_id)
        if state is None:
            raise ToolError("entity has no state")
        attrs = {k: state.attributes[k] for k in READ_ATTRIBUTES if k in state.attributes}
        _LOGGER.info("read %s", plan.entity_id)
        return dumps({"entity_id": plan.entity_id, "state": state.state, "attributes": attrs})

    async def _execute(self, opts: Options, call: ServiceCall) -> str:
        check_call(opts, call)
        if call.service == "set_value":
            self._check_number_range(call)
        _LOGGER.info("MCP call %s.%s %s", call.domain, call.service, call.data)
        await self.hass.services.async_call(
            call.domain, call.service, call.data, blocking=True
        )
        return dumps({"ok": True, "action": f"{call.domain}.{call.service}", "entity_id": call.data["entity_id"]})

    def _check_number_range(self, call: ServiceCall) -> None:
        state = self.hass.states.get(call.data["entity_id"])
        if state is None:
            raise ToolError("entity has no state")
        lo = state.attributes.get("min")
        hi = state.attributes.get("max")
        value = call.data["value"]
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise ToolError("entity does not report min/max; refusing to set")
        if not lo <= value <= hi:
            raise ToolError(f"value must be between {lo:g} and {hi:g}")


class McpView(HomeAssistantView):
    """POST /api/leela_button_presser/mcp (own bearer secret, not HA auth)."""

    url = API_PATH
    name = f"api:{DOMAIN}"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _runtime(self) -> Runtime | None:
        return self.hass.data.get(DOMAIN)

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(status=HTTPStatus.METHOD_NOT_ALLOWED, headers={"Allow": "POST"})

    async def post(self, request: web.Request) -> web.Response:
        rt = self._runtime()
        if rt is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        if not check_bearer(request.headers.get("Authorization"), rt.secret):
            if not rt.auth_failures.allow(MAX_AUTH_FAILURES_PER_MIN):
                return web.Response(status=HTTPStatus.TOO_MANY_REQUESTS)
            _LOGGER.warning("MCP auth failure from %s", request.remote)
            return web.Response(
                status=HTTPStatus.UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if (request.content_length or 0) > MAX_BODY:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        raw = await request.content.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            msg = json.loads(raw)
        except ValueError:
            return self._json({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "parse error"}})
        opts_tools = build_tools(rt.options)
        resp = await handle_message(
            msg,
            lambda: [t.descriptor() for t in opts_tools.values()],
            rt.call_tool,
        )
        if resp is None:
            return web.Response(status=HTTPStatus.ACCEPTED)
        return self._json(resp)

    @staticmethod
    def _json(body: dict[str, Any]) -> web.Response:
        return web.json_response(body)


__all__ = ["McpView", "Runtime", "RATE_LIMITED"]
