"""Button platform for the T-Meter Oil Tank (Local) integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTRIBUTION, DOMAIN, SIGNAL_NEW_DEVICE
from .coordinator import TMeterDataCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a reset-consumption button per device."""
    coordinator: TMeterDataCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    added: set[str] = set()

    @callback
    def _add(device_id: str) -> None:
        if device_id in added:
            return
        added.add(device_id)
        async_add_entities([TMeterResetButton(coordinator, device_id)])

    for device_id in list(coordinator.data):
        _add(device_id)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_DEVICE.format(entry_id=entry.entry_id), _add
        )
    )


class TMeterResetButton(ButtonEntity):
    """Resets the consumption / energy odometer for one tank."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_translation_key = "reset_consumption"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self, coordinator: TMeterDataCoordinator, device_id: str
    ) -> None:
        self._coordinator = coordinator
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_reset_consumption"

    @property
    def _device(self) -> dict[str, Any]:
        return self._coordinator.data.get(self._device_id, {})

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

    async def async_press(self) -> None:
        await self._coordinator.async_reset_consumption(self._device_id)
