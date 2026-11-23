"""Config and options flow with entity selectors."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    API_PATH,
    CONF_BUTTONS,
    CONF_LIGHTS,
    CONF_NUMBERS,
    CONF_PRELOAD,
    CONF_RATE_LIMIT,
    CONF_REGENERATE,
    CONF_ROOMS,
    CONF_SECRET,
    CONF_SENSORS,
    CONF_TIMER_LABEL,
    CONF_TIMERS,
    CONF_VACUUM,
    DEFAULT_RATE_LIMIT,
    DIAL_DEFAULTS,
    DOMAIN,
)
from .tools import parse_rooms


def _multi(domains: list[str] | None) -> selector.EntitySelector:
    cfg: dict[str, Any] = {"multiple": True}
    if domains:
        cfg["domain"] = domains
    return selector.EntitySelector(selector.EntitySelectorConfig(**cfg))


def _options_schema(cur: dict[str, Any], with_regen: bool) -> vol.Schema:
    def d(key: str, default: Any) -> dict[str, Any]:
        value = cur.get(key, default)
        return {"suggested_value": value} if value not in (None, "", []) else {}

    fields: dict[Any, Any] = {
        vol.Optional(CONF_LIGHTS, description=d(CONF_LIGHTS, [])): _multi(["light"]),
        vol.Optional(CONF_BUTTONS, description=d(CONF_BUTTONS, [])): _multi(["button", "input_button"]),
        vol.Optional(CONF_NUMBERS, description=d(CONF_NUMBERS, [])): _multi(["number", "input_number"]),
        vol.Optional(CONF_TIMERS, description=d(CONF_TIMERS, [])): _multi(["timer"]),
        vol.Optional(CONF_TIMER_LABEL, description=d(CONF_TIMER_LABEL, None)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_text")
        ),
        vol.Optional(CONF_VACUUM, description=d(CONF_VACUUM, None)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="vacuum")
        ),
        vol.Optional(CONF_ROOMS, description=d(CONF_ROOMS, "")): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
        vol.Optional(CONF_SENSORS, description=d(CONF_SENSORS, [])): _multi(None),
        vol.Optional(
            CONF_RATE_LIMIT, default=cur.get(CONF_RATE_LIMIT, DEFAULT_RATE_LIMIT)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=600, mode=selector.NumberSelectorMode.BOX)
        ),
    }
    if with_regen:
        fields[vol.Optional(CONF_REGENERATE, default=False)] = bool
    return vol.Schema(fields)


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        parse_rooms(user_input.get(CONF_ROOMS, "") or "")
    except ValueError:
        errors[CONF_ROOMS] = "invalid_rooms"
    if user_input.get(CONF_ROOMS) and not user_input.get(CONF_VACUUM):
        errors[CONF_ROOMS] = "rooms_need_vacuum"
    return errors


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in user_input.items() if k != CONF_REGENERATE}
    if CONF_RATE_LIMIT in out:
        out[CONF_RATE_LIMIT] = int(out[CONF_RATE_LIMIT])
    return out


class LeelaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._secret = secrets.token_urlsafe(32)
        self._options: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            if user_input.get(CONF_PRELOAD):
                self._options = dict(DIAL_DEFAULTS)
            return await self.async_step_secret()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_PRELOAD, default=False): bool}),
        )

    async def async_step_secret(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="Leela Button Presser",
                data={CONF_SECRET: self._secret},
                options=self._options,
            )
        return self.async_show_form(
            step_id="secret",
            data_schema=vol.Schema({}),
            description_placeholders={"secret": self._secret, "path": API_PATH},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LeelaOptionsFlow()


class LeelaOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                if user_input.get(CONF_REGENERATE):
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={**self.config_entry.data, CONF_SECRET: secrets.token_urlsafe(32)},
                    )
                return self.async_create_entry(data=_clean(user_input))
        cur = user_input or dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(cur, with_regen=True),
            errors=errors,
            description_placeholders={
                "secret": str(self.config_entry.data.get(CONF_SECRET, "")),
                "path": API_PATH,
            },
        )
