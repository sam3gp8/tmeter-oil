"""Binary sensor platform for the T-Meter Oil Tank (Local) integration."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_OFFLINE_AFTER,
    DEFAULT_OFFLINE_AFTER,
    DOMAIN,
    SIGNAL_NEW_DEVICE,
)
from .coordinator import TMeterDataCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a connectivity sensor per device as devices are discovered."""
    coordinator: TMeterDataCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    offline_after = int(entry.options.get(CONF_OFFLINE_AFTER, DEFAULT_OFFLINE_AFTER))
    added: set[str] = set()

    @callback
    def _add(device_id: str) -> None:
        if device_id in added:
            return
        added.add(device_id)
        async_add_entities(
            [TMeterConnectivity(coordinator, device_id, offline_after)]
        )

    for device_id in list(coordinator.data):
        _add(device_id)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_DEVICE.format(entry_id=entry.entry_id), _add
        )
    )


class TMeterConnectivity(
    CoordinatorEntity[TMeterDataCoordinator], BinarySensorEntity
):
    """Connectivity, optionally driven by a staleness window.

    With ``offline_after_minutes == 0`` the device is considered connected once
    any report has been received. With a positive value the sensor turns off if
    no frame arrives within that window (re-evaluated once a minute), which is
    useful once the device's reporting interval is known.
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: TMeterDataCoordinator,
        device_id: str,
        offline_after_minutes: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._offline_after = max(0, offline_after_minutes)
        self._attr_unique_id = f"{device_id}_connectivity"

    @property
    def _device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._device_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._device
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=f"Oil Tank {self._device_id}",
            manufacturer="Dayan / T-Meter",
            model="Oil Tank Sensor (local)",
            serial_number=self._device_id,
            sw_version=dev.get("version"),
        )

    @property
    def is_on(self) -> bool | None:
        last_seen = self._device.get("last_seen_ts")
        if last_seen is None:
            return None
        if self._offline_after <= 0:
            return True
        return (time.time() - float(last_seen)) <= self._offline_after * 60

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._offline_after > 0:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass, self._recheck, timedelta(minutes=1)
                )
            )

    @callback
    def _recheck(self, _now) -> None:
        self.async_write_ha_state()
