"""Config flow for the T-Meter Oil Tank (Local) integration."""

from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_CLOUD_HOST,
    CONF_CLOUD_PORT,
    CONF_ENABLE_PASSTHROUGH,
    CONF_FORWARD_CLOUD,
    CONF_HOST,
    CONF_INVERT_LEVEL,
    CONF_KWH_PER_GALLON,
    CONF_OFFLINE_AFTER,
    CONF_PASSTHROUGH_PORT,
    CONF_PORT,
    CONF_REFILL_THRESHOLD,
    CONF_TANK_GALLONS,
    DEFAULT_CLOUD_HOST,
    DEFAULT_CLOUD_PORT,
    DEFAULT_ENABLE_PASSTHROUGH,
    DEFAULT_FORWARD_CLOUD,
    DEFAULT_HOST,
    DEFAULT_INVERT_LEVEL,
    DEFAULT_KWH_PER_GALLON,
    DEFAULT_OFFLINE_AFTER,
    DEFAULT_PASSTHROUGH_PORT,
    DEFAULT_PORT,
    DEFAULT_REFILL_THRESHOLD,
    DEFAULT_TANK_GALLONS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _test_bind(host: str, port: int) -> None:
    """Raise OSError if we cannot bind the listen port (runs in executor)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    finally:
        sock.close()


class TMeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow; one listener can serve multiple tanks."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.get(CONF_HOST, DEFAULT_HOST)
            port = user_input[CONF_PORT]
            try:
                await self.hass.async_add_executor_job(_test_bind, host, port)
            except OSError:
                errors["base"] = "port_in_use"
            else:
                return self.async_create_entry(
                    title=f"T-Meter Oil Tank (port {port})",
                    data={CONF_HOST: host, CONF_PORT: port},
                    options={
                        CONF_TANK_GALLONS: user_input.get(
                            CONF_TANK_GALLONS, DEFAULT_TANK_GALLONS
                        ),
                        CONF_KWH_PER_GALLON: user_input.get(
                            CONF_KWH_PER_GALLON, DEFAULT_KWH_PER_GALLON
                        ),
                        CONF_FORWARD_CLOUD: user_input.get(
                            CONF_FORWARD_CLOUD, DEFAULT_FORWARD_CLOUD
                        ),
                        CONF_CLOUD_HOST: user_input.get(
                            CONF_CLOUD_HOST, DEFAULT_CLOUD_HOST
                        ),
                        CONF_CLOUD_PORT: user_input.get(
                            CONF_CLOUD_PORT, DEFAULT_CLOUD_PORT
                        ),
                        CONF_OFFLINE_AFTER: user_input.get(
                            CONF_OFFLINE_AFTER, DEFAULT_OFFLINE_AFTER
                        ),
                        CONF_REFILL_THRESHOLD: user_input.get(
                            CONF_REFILL_THRESHOLD, DEFAULT_REFILL_THRESHOLD
                        ),
                        CONF_INVERT_LEVEL: user_input.get(
                            CONF_INVERT_LEVEL, DEFAULT_INVERT_LEVEL
                        ),
                        CONF_ENABLE_PASSTHROUGH: user_input.get(
                            CONF_ENABLE_PASSTHROUGH, DEFAULT_ENABLE_PASSTHROUGH
                        ),
                        CONF_PASSTHROUGH_PORT: user_input.get(
                            CONF_PASSTHROUGH_PORT, DEFAULT_PASSTHROUGH_PORT
                        ),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                        int, vol.Range(min=1, max=65535)
                    ),
                    vol.Optional(
                        CONF_TANK_GALLONS, default=DEFAULT_TANK_GALLONS
                    ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100000)),
                    vol.Optional(
                        CONF_KWH_PER_GALLON, default=DEFAULT_KWH_PER_GALLON
                    ): vol.All(vol.Coerce(float), vol.Range(min=1, max=200)),
                    vol.Optional(
                        CONF_FORWARD_CLOUD, default=DEFAULT_FORWARD_CLOUD
                    ): bool,
                    vol.Optional(
                        CONF_INVERT_LEVEL, default=DEFAULT_INVERT_LEVEL
                    ): bool,
                    vol.Optional(
                        CONF_ENABLE_PASSTHROUGH,
                        default=DEFAULT_ENABLE_PASSTHROUGH,
                    ): bool,
                    vol.Optional(CONF_HOST, default=DEFAULT_HOST): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TMeterOptionsFlow:
        return TMeterOptionsFlow(config_entry)


class TMeterOptionsFlow(OptionsFlow):
    """Tune tank size, energy factor, cloud forwarding, and staleness."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self._entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TANK_GALLONS,
                        default=opts.get(CONF_TANK_GALLONS, DEFAULT_TANK_GALLONS),
                    ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100000)),
                    vol.Optional(
                        CONF_KWH_PER_GALLON,
                        default=opts.get(CONF_KWH_PER_GALLON, DEFAULT_KWH_PER_GALLON),
                    ): vol.All(vol.Coerce(float), vol.Range(min=1, max=200)),
                    vol.Optional(
                        CONF_FORWARD_CLOUD,
                        default=opts.get(CONF_FORWARD_CLOUD, DEFAULT_FORWARD_CLOUD),
                    ): bool,
                    vol.Optional(
                        CONF_CLOUD_HOST,
                        default=opts.get(CONF_CLOUD_HOST, DEFAULT_CLOUD_HOST),
                    ): str,
                    vol.Optional(
                        CONF_CLOUD_PORT,
                        default=opts.get(CONF_CLOUD_PORT, DEFAULT_CLOUD_PORT),
                    ): vol.All(int, vol.Range(min=1, max=65535)),
                    vol.Optional(
                        CONF_OFFLINE_AFTER,
                        default=opts.get(CONF_OFFLINE_AFTER, DEFAULT_OFFLINE_AFTER),
                    ): vol.All(int, vol.Range(min=0, max=10080)),
                    vol.Optional(
                        CONF_REFILL_THRESHOLD,
                        default=opts.get(
                            CONF_REFILL_THRESHOLD, DEFAULT_REFILL_THRESHOLD
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100000)),
                    vol.Optional(
                        CONF_INVERT_LEVEL,
                        default=opts.get(CONF_INVERT_LEVEL, DEFAULT_INVERT_LEVEL),
                    ): bool,
                    vol.Optional(
                        CONF_ENABLE_PASSTHROUGH,
                        default=opts.get(
                            CONF_ENABLE_PASSTHROUGH, DEFAULT_ENABLE_PASSTHROUGH
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_PASSTHROUGH_PORT,
                        default=opts.get(
                            CONF_PASSTHROUGH_PORT, DEFAULT_PASSTHROUGH_PORT
                        ),
                    ): vol.All(int, vol.Range(min=1, max=65535)),
                }
            ),
        )
