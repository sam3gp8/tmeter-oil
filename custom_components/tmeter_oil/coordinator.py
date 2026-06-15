"""Push data coordinator for the T-Meter Oil Tank (Local) integration."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONSUMPTION_NOISE_FLOOR_GAL,
    DOMAIN,
    EVENT_REFILL,
    LITERS_PER_GALLON,
    SIGNAL_NEW_DEVICE,
    compute_levels,
    parse_frame,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class TMeterDataCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Holds the latest reading per device and a persistent consumption odometer.

    This is a push coordinator: there is no polling. The TCP server calls
    :meth:`async_handle_frame` whenever the device reports, which updates the
    data and notifies entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        rated_gallons: float,
        kwh_per_gallon: float,
        refill_threshold: float,
        orientation: str,
        tank_height_in: float,
        raw_divisor: float,
        gal_per_inch: float,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN)
        self.entry = entry
        self._rated_gallons = rated_gallons
        self._kwh_per_gallon = kwh_per_gallon
        self._refill_threshold = refill_threshold
        self._orientation = orientation
        self._tank_height_in = tank_height_in
        self._raw_divisor = raw_divisor
        self._gal_per_inch = gal_per_inch
        self.data = {}
        # Persistent odometer: {device_id: {"last_gal": float, "consumed_gal": float}}
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        self._acc: dict[str, dict[str, float]] = {}

    async def async_load_state(self) -> None:
        """Load the persisted odometer before serving starts."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._acc = {k: dict(v) for k, v in stored.items()}

    async def async_handle_frame(self, raw: bytes, source: str) -> None:
        """Parse one frame, update derived values, and notify listeners."""
        raw_fields = parse_frame(raw)
        if not raw_fields:
            return
        parsed = compute_levels(
            raw_fields,
            orientation=self._orientation,
            tank_height_in=self._tank_height_in,
            raw_divisor=self._raw_divisor,
            gal_per_inch=self._gal_per_inch,
            rated_gallons=self._rated_gallons,
        )

        device_id = parsed["device_id"]
        now = time.time()
        parsed["last_seen_ts"] = now
        parsed["last_report"] = _isoformat(now)
        parsed["source"] = source

        self._accumulate(device_id, parsed)

        is_new = device_id not in self.data
        self.data[device_id] = parsed

        try:
            await self._store.async_save(self._acc)
        except Exception:  # pragma: no cover - storage best effort
            _LOGGER.debug("Could not persist odometer", exc_info=True)

        if is_new:
            _LOGGER.info("Discovered T-Meter device %s from %s", device_id, source)
            async_dispatcher_send(
                self.hass,
                SIGNAL_NEW_DEVICE.format(entry_id=self.entry.entry_id),
                device_id,
            )

        self.async_set_updated_data(self.data)
        _LOGGER.debug(
            "%s level=%s%% (%s gal) temp=%s rssi=%s flag=%s consumed=%s gal",
            device_id, parsed["level"], parsed["gallons"], parsed["temp_c"],
            parsed["rssi"], parsed["flag"], parsed.get("consumed_gal"),
        )

    async def async_reset_consumption(self, device_id: str) -> None:
        """Zero the consumption odometer for a device (keeps current level)."""
        state = self._acc.setdefault(
            device_id, {"last_gal": None, "consumed_gal": 0.0}
        )
        state["consumed_gal"] = 0.0
        try:
            await self._store.async_save(self._acc)
        except Exception:  # pragma: no cover
            _LOGGER.debug("Could not persist odometer reset", exc_info=True)
        if device_id in self.data:
            self.data[device_id]["consumed_gal"] = 0.0
            self.data[device_id]["energy_kwh"] = 0.0
            self.async_set_updated_data(self.data)
        _LOGGER.info("Reset consumption odometer for %s", device_id)

    def _accumulate(self, device_id: str, parsed: dict[str, Any]) -> None:
        """Maintain a monotonic gallons-consumed odometer from level drops.

        A drop beyond the noise floor counts as consumption; a large rise is a
        refill/delivery and only resets the baseline.
        """
        rem_gal = parsed.get("gallons")
        state = self._acc.setdefault(device_id, {"last_gal": None, "consumed_gal": 0.0})

        if rem_gal is not None:
            last = state.get("last_gal")
            if last is not None:
                drop = last - rem_gal
                rise = rem_gal - last
                if drop > CONSUMPTION_NOISE_FLOOR_GAL:
                    state["consumed_gal"] = state.get("consumed_gal", 0.0) + drop
                elif rise >= self._refill_threshold:
                    self._fire_refill(device_id, rise, parsed)
            state["last_gal"] = rem_gal

        consumed_gal = round(state.get("consumed_gal", 0.0) or 0.0, 2)
        parsed["consumed_gal"] = consumed_gal
        parsed["energy_kwh"] = round(consumed_gal * self._kwh_per_gallon, 2)

    def _fire_refill(
        self, device_id: str, gallons_added: float, parsed: dict[str, Any]
    ) -> None:
        """Fire a delivery/refill event on the HA bus."""
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, device_id)}
        )
        data = {
            "device_id": device.id if device else None,
            "tank_id": device_id,
            "gallons_added": round(gallons_added, 1),
            "liters_added": round(gallons_added * LITERS_PER_GALLON, 1),
            "level": parsed.get("level"),
            "gallons": parsed.get("gallons"),
        }
        _LOGGER.info(
            "Refill detected on %s: +%.1f gal (now %s%%)",
            device_id, gallons_added, parsed.get("level"),
        )
        self.hass.bus.async_fire(EVENT_REFILL, data)


def _isoformat(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch))
