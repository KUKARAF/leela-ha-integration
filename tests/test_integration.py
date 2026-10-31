"""End-to-end through a real (test) Home Assistant, config flow and HTTP view."""

import json

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.leela_button_presser.const import API_PATH, DIAL_DEFAULTS, DOMAIN

SECRET = "s3cret-test-value"
H = {"Authorization": f"Bearer {SECRET}"}
DESK = "light.desk_light_desk_light"


async def _setup(hass: HomeAssistant, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data={"secret": SECRET}, options=options if options is not None else dict(DIAL_DEFAULTS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _call(name, args, i=1):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


async def test_auth_and_protocol(hass, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    await _setup(hass)
    c = await hass_client_no_auth()
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status == 401
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": "Bearer wrong"})
    assert r.status == 401
    # An HA token is not accepted either.
    r = await c.get(API_PATH, headers=H)
    assert r.status == 405
    r = await c.post(API_PATH, data=b"{nope", headers=H)
    assert (await r.json())["error"]["code"] == -32700
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, headers=H)
    assert (await r.json())["result"]["serverInfo"]["name"] == "leela-button-presser"
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=H)
    assert r.status == 202
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=H)
    names = {t["name"] for t in (await r.json())["result"]["tools"]}
    assert {"light_on", "vacuum_clean_room", "get_state"} <= names


async def test_tool_calls_hit_services(hass, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    await _setup(hass)
    on = async_mock_service(hass, "light", "turn_on")
    vac = async_mock_service(hass, "vacuum", "send_command")
    btn = async_mock_service(hass, "button", "press")
    other = async_mock_service(hass, "lock", "unlock")
    c = await hass_client_no_auth()

    r = await c.post(API_PATH, json=_call("light_set_brightness", {"light": DESK, "brightness": 77}), headers=H)
    body = await r.json()
    assert body["result"]["isError"] is False
    assert len(on) == 1 and on[0].data == {"entity_id": DESK, "brightness": 77}

    r = await c.post(API_PATH, json=_call("vacuum_clean_room", {"room": "bedroom"}), headers=H)
    assert (await r.json())["result"]["isError"] is False
    assert vac[0].data["params"] == [{"segments": [19], "repeat": 1}]

    r = await c.post(API_PATH, json=_call("button_press", {"button": "button.fmd_rafa_volume_ring_device"}), headers=H)
    assert len(btn) == 1

    # Rejected: bad entity, bad range, unknown extra arg, unknown tool.
    for args in ({"light": "light.evil"}, {"light": DESK, "brightness": 300}, {"light": DESK, "x": 1}):
        r = await c.post(API_PATH, json=_call("light_on", args), headers=H)
        assert (await r.json())["result"]["isError"] is True
    r = await c.post(API_PATH, json=_call("lock_unlock", {"entity_id": "lock.front"}), headers=H)
    assert "error" in await r.json()
    assert len(on) == 1 and not other


async def test_read_tools(hass, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    await _setup(hass)
    hass.states.async_set(DESK, "on", {"brightness": 128, "secret_attr": "x"})
    hass.states.async_set("sensor.mt15_mt29_carbon_dioxide", "812", {"unit_of_measurement": "ppm"})
    hass.states.async_set("lock.front", "locked")
    c = await hass_client_no_auth()
    r = await c.post(API_PATH, json=_call("get_state", {"entity": DESK}), headers=H)
    out = json.loads((await r.json())["result"]["content"][0]["text"])
    assert out["state"] == "on" and out["attributes"] == {"brightness": 128}
    r = await c.post(API_PATH, json=_call("get_state", {"entity": "sensor.mt15_mt29_carbon_dioxide"}), headers=H)
    assert json.loads((await r.json())["result"]["content"][0]["text"])["state"] == "812"
    r = await c.post(API_PATH, json=_call("get_state", {"entity": "lock.front"}), headers=H)
    assert (await r.json())["result"]["isError"] is True


async def test_number_range_from_entity(hass, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    await _setup(hass, {"numbers": ["input_number.dial"]})
    calls = async_mock_service(hass, "input_number", "set_value")
    hass.states.async_set("input_number.dial", "5", {"min": 0, "max": 10})
    c = await hass_client_no_auth()
    r = await c.post(API_PATH, json=_call("number_set", {"number": "input_number.dial", "value": 7}), headers=H)
    assert (await r.json())["result"]["isError"] is False and len(calls) == 1
    r = await c.post(API_PATH, json=_call("number_set", {"number": "input_number.dial", "value": 11}), headers=H)
    assert (await r.json())["result"]["isError"] is True and len(calls) == 1


async def test_rate_limit(hass, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    await _setup(hass, {"lights": [DESK], "rate_limit_per_minute": 2})
    calls = async_mock_service(hass, "light", "toggle")
    c = await hass_client_no_auth()
    res = []
    for i in range(4):
        r = await c.post(API_PATH, json=_call("light_toggle", {"light": DESK}, i), headers=H)
        res.append((await r.json())["result"]["isError"])
    assert res == [False, False, True, True] and len(calls) == 2


async def test_options_apply_live_and_unloaded(hass, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    entry = await _setup(hass)
    c = await hass_client_no_auth()
    hass.config_entries.async_update_entry(entry, options={"lights": [DESK]})
    await hass.async_block_till_done()
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=H)
    names = {t["name"] for t in (await r.json())["result"]["tools"]}
    assert "vacuum_start" not in names and "light_on" in names
    assert await hass.config_entries.async_unload(entry.entry_id)
    r = await c.post(API_PATH, json={"jsonrpc": "2.0", "id": 2, "method": "ping"}, headers=H)
    assert r.status == 404


async def test_config_flow_and_options(hass):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert res["type"] == data_entry_flow.FlowResultType.FORM
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {"preload_dial_defaults": True})
    assert res["step_id"] == "secret"
    secret = res["description_placeholders"]["secret"]
    assert len(secret) >= 40
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {})
    assert res["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = res["result"]
    assert entry.data == {"secret": secret}
    assert entry.options["vacuum"] == "vacuum.juanita"
    await hass.async_block_till_done()

    res = await hass.config_entries.options.async_init(entry.entry_id)
    assert res["type"] == data_entry_flow.FlowResultType.FORM
    bad = await hass.config_entries.options.async_configure(res["flow_id"], {"lights": [DESK], "rooms": "nonsense", "vacuum": "vacuum.juanita"})
    assert bad["errors"] == {"rooms": "invalid_rooms"}
    ok = await hass.config_entries.options.async_configure(res["flow_id"], {"lights": [DESK], "rooms": "hall=3", "vacuum": "vacuum.juanita", "rate_limit_per_minute": 10, "regenerate_secret": True})
    assert ok["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options["rooms"] == "hall=3" and entry.options["rate_limit_per_minute"] == 10
    assert entry.data["secret"] != secret

    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert res["type"] == data_entry_flow.FlowResultType.ABORT
