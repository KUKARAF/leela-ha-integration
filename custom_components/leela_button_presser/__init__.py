"""Leela Button Presser: an MCP server inside Home Assistant with a UI allowlist."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import McpView, Runtime
from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not hass.data.get(f"{DOMAIN}_view"):
        hass.http.register_view(McpView(hass))
        hass.data[f"{DOMAIN}_view"] = True
    hass.data[DOMAIN] = Runtime(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.pop(DOMAIN, None)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Options are read live; just refresh the runtime's entry reference.
    rt = hass.data.get(DOMAIN)
    if rt is not None:
        rt.entry = entry
