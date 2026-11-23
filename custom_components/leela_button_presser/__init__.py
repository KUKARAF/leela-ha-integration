"""Leela Button Presser: an MCP server inside Home Assistant with a UI allowlist."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback

from .api import McpView, Runtime
from .const import DOMAIN


LABEL_CLEAR_EVENTS = ("timer.finished", "timer.cancelled")


@callback
def _clear_label(hass: HomeAssistant, event: Event) -> None:
    """Blank the timer label text when an allowlisted timer ends."""
    rt = hass.data.get(DOMAIN)
    if rt is None:
        return
    opts = rt.options
    if not opts.timer_label or event.data.get("entity_id") not in opts.timers:
        return
    hass.async_create_task(
        hass.services.async_call(
            "input_text",
            "set_value",
            {"entity_id": opts.timer_label, "value": ""},
        )
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not hass.data.get(f"{DOMAIN}_view"):
        hass.http.register_view(McpView(hass))
        hass.data[f"{DOMAIN}_view"] = True
    hass.data[DOMAIN] = Runtime(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_reload))

    @callback
    def _on_timer_end(event: Event) -> None:
        _clear_label(hass, event)

    for name in LABEL_CLEAR_EVENTS:
        entry.async_on_unload(hass.bus.async_listen(name, _on_timer_end))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.pop(DOMAIN, None)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Options are read live; just refresh the runtime's entry reference.
    rt = hass.data.get(DOMAIN)
    if rt is not None:
        rt.entry = entry
