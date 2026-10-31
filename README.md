# Leela Button Presser

A Home Assistant custom integration that serves an MCP endpoint (streamable HTTP, JSON-RPC) from inside HA. Tools are backed by `hass.services.async_call` and limited to an allowlist you pick in the UI. It has its own bearer secret, so no HA long-lived or admin token ever leaves HA.

## Install

1. Copy `custom_components/leela_button_presser` to `<config>/custom_components/` (or add this repo to HACS as a custom repository). Restart HA.
2. Settings -> Devices & services -> Add integration -> "Leela Button Presser". Optionally tick "Preload the M5 Dial entities".
3. Copy the secret shown on the next screen (also visible under Configure, which is admin-only).
4. Click Configure and choose the allowlist:
   - Lights: `light_on` (optional brightness 1-255), `light_off`, `light_toggle`, `light_set_brightness` (0-255, 0 = off)
   - Buttons / input_button: `button_press`
   - number / input_number: `number_set` (value must be within the entity's own min/max)
   - Timers: `timer_start`
   - Vacuum + rooms (`name=segment_id`, one per line): `vacuum_start`, `vacuum_clean_room` (`vacuum.send_command` `app_segment_clean`, repeat 1)
   - Read-only: `get_state` / `list_entities` over lights, numbers, timers, vacuum and any extra sensors (CO2, formaldehyde, ...)
   - Max tool calls per minute (default 30)

The M5 Dial preset covers the 4 lights, `vacuum.juanita` with its 7 rooms, `timer.egg`, `button.fmd_rafa_volume_ring_device`, and the CO2/formaldehyde sensors. The Dial does not use dock/pause, so those tools do not exist.

Options changes apply immediately, with no reload.

## Endpoint

`POST https://<ha-host>/api/leela_button_presser/mcp` with `Authorization: Bearer <secret>`. JSON responses only (no SSE, no sessions, no batching). Notifications return 202.

## LiteLLM config.yaml

```yaml
mcp_servers:
  home_assistant:
    url: "https://ha.example.com/api/leela_button_presser/mcp"
    transport: "http"
    auth_type: "bearer_token"
    auth_value: "os.environ/LEELA_HA_MCP_SECRET"
```

(If your LiteLLM version lacks `auth_type`, send the header via `headers: {Authorization: "Bearer ..."}`.) The chat bot's client at `chat/src/bot/mcp.rs` sends the bearer in `Authorization`.

## Security

- The view has `requires_auth = False` and does its own check: constant-time bearer compare against a 256-bit random secret stored in the config entry. More than 10 failed attempts per minute get 429.
- Calls are checked twice: parameters are validated against the tool schema (enums of configured entities, integer ranges, unknown arguments rejected), then a final guard re-verifies service, data keys, entity and domain, and (vacuum) the exact command and segment id against live options.
- Rate limit per minute across all tools; every call is logged at INFO (`custom_components.leela_button_presser`).
- Read-only tools expose only a short list of safe attributes. Anyone holding the secret can do everything in the allowlist, so keep it in LiteLLM's env, and expose HA over TLS only. Regenerate it under Configure.

## Tests

```
uv venv --python 3.13 .venv && uv pip install -p .venv pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```
