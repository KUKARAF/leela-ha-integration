"""Pure logic tests: allowlist, validation, MCP protocol (no HA instance)."""

import json

import pytest

from custom_components.leela_button_presser.const import DIAL_DEFAULTS
from custom_components.leela_button_presser.mcp import (
    RateLimiter,
    check_bearer,
    handle_message,
)
from custom_components.leela_button_presser.tools import (
    ReadCall,
    ServiceCall,
    ToolError,
    build_tools,
    check_call,
    parse_options,
    parse_rooms,
)

OPTS = parse_options(DIAL_DEFAULTS | {"numbers": ["input_number.dial"]})
TOOLS = build_tools(OPTS)
DESK = "light.desk_light_desk_light"


def test_dial_toolset():
    assert set(TOOLS) == {
        "light_on", "light_off", "light_toggle", "light_set_brightness",
        "button_press", "number_set", "timer_start", "vacuum_start",
        "vacuum_clean_room", "get_state", "list_entities",
    }
    assert len(OPTS.rooms) == 7 and OPTS.rooms["bedroom"] == 19


def test_empty_options_no_tools():
    assert build_tools(parse_options({})) == {}


def test_light_plans():
    p = TOOLS["light_set_brightness"].plan({"light": DESK, "brightness": 128})
    assert p == ServiceCall("light", "turn_on", {"entity_id": DESK, "brightness": 128})
    p = TOOLS["light_set_brightness"].plan({"light": DESK, "brightness": 0})
    assert p.service == "turn_off"
    p = TOOLS["light_on"].plan({"light": DESK})
    assert p.data == {"entity_id": DESK}
    assert TOOLS["light_toggle"].plan({"light": DESK}).service == "toggle"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("light_on", {"light": "light.not_allowed"}),
        ("light_on", {"light": DESK, "brightness": 256}),
        ("light_on", {"light": DESK, "brightness": 0}),
        ("light_set_brightness", {"light": DESK, "brightness": -1}),
        ("light_set_brightness", {"light": DESK, "brightness": 1.5}),
        ("light_set_brightness", {"light": DESK, "brightness": True}),
        ("light_set_brightness", {"light": DESK, "brightness": "9"}),
        ("light_set_brightness", {"light": DESK}),
        ("light_off", {"light": DESK, "extra": 1}),
        ("light_off", {}),
        ("vacuum_clean_room", {"room": "attic"}),
        ("button_press", {"button": "switch.mains"}),
        ("number_set", {"number": "input_number.dial", "value": float("nan")}),
        ("get_state", {"entity": "lock.front_door"}),
    ],
)
def test_rejects(tool, args):
    with pytest.raises(ToolError):
        TOOLS[tool].plan(args)


def test_integral_float_ok():
    p = TOOLS["light_on"].plan({"light": DESK, "brightness": 200.0})
    assert p.data["brightness"] == 200 and isinstance(p.data["brightness"], int)


def test_room_clean_matches_dial():
    p = TOOLS["vacuum_clean_room"].plan({"room": "kitchen"})
    assert p == ServiceCall(
        "vacuum", "send_command",
        {"entity_id": "vacuum.juanita", "command": "app_segment_clean",
         "params": [{"segments": [24], "repeat": 1}]},
    )
    check_call(OPTS, p)
    assert TOOLS["vacuum_start"].plan({}) == ServiceCall("vacuum", "start", {"entity_id": "vacuum.juanita"})
    assert TOOLS["timer_start"].plan({"timer": "timer.egg"}).domain == "timer"
    b = TOOLS["button_press"].plan({"button": "button.fmd_rafa_volume_ring_device"})
    assert (b.domain, b.service) == ("button", "press")


def test_read_plans():
    assert TOOLS["get_state"].plan({"entity": "sensor.mt15_mt29_carbon_dioxide"}) == ReadCall("sensor.mt15_mt29_carbon_dioxide")
    assert TOOLS["list_entities"].plan(None) == ReadCall(None)


@pytest.mark.parametrize(
    "call",
    [
        ServiceCall("light", "turn_on", {"entity_id": "light.other"}),
        ServiceCall("light", "turn_on", {"entity_id": DESK, "color_name": "red"}),
        ServiceCall("light", "turn_on", {"entity_id": DESK, "brightness": 999}),
        ServiceCall("homeassistant", "restart", {}),
        ServiceCall("lock", "unlock", {"entity_id": "lock.front"}),
        ServiceCall("light", "turn_on", {"entity_id": "timer.egg"}),
        ServiceCall("vacuum", "send_command", {"entity_id": "vacuum.juanita", "command": "app_goto_target", "params": []}),
        ServiceCall("vacuum", "send_command", {"entity_id": "vacuum.juanita", "command": "app_segment_clean", "params": [{"segments": [999], "repeat": 1}]}),
        ServiceCall("vacuum", "send_command", {"entity_id": "vacuum.juanita", "command": "app_segment_clean", "params": [{"segments": [16], "repeat": 50}]}),
        ServiceCall("vacuum", "start", {"entity_id": "sensor.mt15_mt29_carbon_dioxide"}),
    ],
)
def test_check_call_blocks(call):
    with pytest.raises(ToolError):
        check_call(OPTS, call)


def test_options_garbage_tolerated():
    o = parse_options({"lights": ["switch.x", "light.ok", 5, "bad id"], "vacuum": "light.x", "rooms": "bad", "rate_limit_per_minute": "zz"})
    assert o.lights == ("light.ok",) and o.vacuum is None and o.rooms == {} and o.rate_limit == 30


def test_parse_rooms():
    assert parse_rooms("Rafas Office=18\nkitchen = 24") == {"rafas_office": 18, "kitchen": 24}
    for bad in ["x", "x=", "x=-1", "x=0", "=5", "a b!=1", "x=1.5"]:
        with pytest.raises(ValueError):
            parse_rooms(bad)


def test_bearer():
    assert check_bearer("Bearer abc", "abc")
    assert check_bearer("bearer abc", "abc")
    assert not check_bearer("Bearer abd", "abc")
    assert not check_bearer("Basic abc", "abc")
    assert not check_bearer(None, "abc")
    assert not check_bearer("Bearer ", "")
    assert not check_bearer("Bearer x", "")


def test_rate_limiter():
    t = [0.0]
    rl = RateLimiter(lambda: t[0])
    assert all(rl.allow(3) for _ in range(3))
    assert not rl.allow(3)
    t[0] = 61
    assert rl.allow(3)


async def _rpc(msg):
    async def call(name, args):
        return None if name != "x" else {"content": [], "isError": False}

    return await handle_message(msg, lambda: [{"name": "t"}], call)


async def test_mcp_protocol():
    r = await _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert r["result"]["protocolVersion"] == "2025-03-26" and "tools" in r["result"]["capabilities"]
    r = await _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999"}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert await _rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert (await _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))["result"] == {"tools": [{"name": "t"}]}
    assert (await _rpc({"jsonrpc": "2.0", "id": 3, "method": "ping"}))["result"] == {}
    assert (await _rpc({"jsonrpc": "2.0", "id": 4, "method": "nope"}))["error"]["code"] == -32601
    assert (await _rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "zz"}}))["error"]["code"] == -32602
    assert (await _rpc([{"jsonrpc": "2.0"}]))["error"]["code"] == -32600
    assert (await _rpc({"id": 1}))["error"]["code"] == -32600
