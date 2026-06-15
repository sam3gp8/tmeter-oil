"""The T-Meter Oil Tank (Local) integration.

Listens for the oil-tank sensor's raw TCP telemetry on a local port, replaces
the vendor cloud, and exposes native Home Assistant entities — including a
cumulative energy sensor for the Energy Dashboard. No cloud, no TLS, no
firmware changes.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_CLOUD_HOST,
    CONF_CLOUD_PORT,
    CONF_ENABLE_PASSTHROUGH,
    CONF_FORWARD_CLOUD,
    CONF_HOST,
    CONF_INVERT_LEVEL,
    CONF_KWH_PER_GALLON,
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
    DEFAULT_PASSTHROUGH_PORT,
    DEFAULT_PORT,
    DEFAULT_REFILL_THRESHOLD,
    DEFAULT_TANK_GALLONS,
    DOMAIN,
)
from .coordinator import TMeterDataCoordinator
from .passthrough import TMeterPassthrough
from .server import TMeterServer

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up T-Meter Oil Tank (Local) from a config entry."""
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    tank_gallons = entry.options.get(CONF_TANK_GALLONS, DEFAULT_TANK_GALLONS)
    kwh_per_gallon = entry.options.get(CONF_KWH_PER_GALLON, DEFAULT_KWH_PER_GALLON)
    forward_cloud = entry.options.get(CONF_FORWARD_CLOUD, DEFAULT_FORWARD_CLOUD)
    cloud_host = entry.options.get(CONF_CLOUD_HOST, DEFAULT_CLOUD_HOST)
    cloud_port = entry.options.get(CONF_CLOUD_PORT, DEFAULT_CLOUD_PORT)
    refill_threshold = entry.options.get(
        CONF_REFILL_THRESHOLD, DEFAULT_REFILL_THRESHOLD
    )
    invert_level = entry.options.get(CONF_INVERT_LEVEL, DEFAULT_INVERT_LEVEL)
    enable_passthrough = entry.options.get(
        CONF_ENABLE_PASSTHROUGH, DEFAULT_ENABLE_PASSTHROUGH
    )
    passthrough_port = entry.options.get(
        CONF_PASSTHROUGH_PORT, DEFAULT_PASSTHROUGH_PORT
    )

    coordinator = TMeterDataCoordinator(
        hass,
        entry,
        tank_gallons=tank_gallons,
        kwh_per_gallon=kwh_per_gallon,
        refill_threshold=refill_threshold,
        invert_level=invert_level,
    )
    await coordinator.async_load_state()

    server = TMeterServer(
        hass,
        coordinator.async_handle_frame,
        host=host,
        port=port,
        forward_cloud=forward_cloud,
        cloud_host=cloud_host,
        cloud_port=cloud_port,
    )
    try:
        await server.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"Could not bind T-Meter listener on {host}:{port}: {err}"
        ) from err

    # Optional transparent 443 relay so the vendor app keeps working. This is
    # best-effort: if the port can't be bound (privileged/in use) we log and
    # continue — the device listener above is what actually matters.
    passthrough: TMeterPassthrough | None = None
    if enable_passthrough:
        passthrough = TMeterPassthrough(
            listen_host=host,
            listen_port=passthrough_port,
            target_host=cloud_host,
            target_port=passthrough_port,
        )
        try:
            await passthrough.async_start()
        except OSError as err:
            _LOGGER.warning(
                "App passthrough on port %d could not start (%s). The tank still "
                "works; the phone app will not until this port is available.",
                passthrough_port, err,
            )
            passthrough = None

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "server": server,
        "passthrough": passthrough,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and stop the listener."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        await data["server"].async_stop()
        if data.get("passthrough"):
            await data["passthrough"].async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change (rebinds the listener, re-reads tank size)."""
    await hass.config_entries.async_reload(entry.entry_id)
