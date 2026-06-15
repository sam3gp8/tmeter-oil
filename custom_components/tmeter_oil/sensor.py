"""Sensor platform for the T-Meter Oil Tank (Local) integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfVolume,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN, SIGNAL_NEW_DEVICE
from .coordinator import TMeterDataCoordinator


@dataclass(frozen=True, kw_only=True)
class TMeterSensorDescription(SensorEntityDescription):
    """Sensor description with a reader over the per-device value dict."""

    value_fn: Callable[[dict[str, Any]], Any]
    cumulative: bool = False


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return dt_util.parse_datetime(str(value))
    except (TypeError, ValueError):
        return None


SENSORS: tuple[TMeterSensorDescription, ...] = (
    TMeterSensorDescription(
        key="level",
        translation_key="level",
        icon="mdi:gauge",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("level"),
    ),
    TMeterSensorDescription(
        key="oil_height",
        translation_key="oil_height",
        icon="mdi:waves-arrow-up",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.INCHES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("oil_in"),
    ),
    TMeterSensorDescription(
        key="air_height",
        translation_key="air_height",
        icon="mdi:arrow-expand-up",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.INCHES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("air_in"),
    ),
    TMeterSensorDescription(
        key="device_pct",
        translation_key="device_pct",
        icon="mdi:gauge-low",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("device_pct"),
    ),
    TMeterSensorDescription(
        key="gallons",
        translation_key="gallons",
        icon="mdi:barrel",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("gallons"),
    ),
    TMeterSensorDescription(
        key="liters",
        translation_key="liters",
        icon="mdi:barrel-outline",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("liters"),
    ),
    TMeterSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("temp_c"),
    ),
    TMeterSensorDescription(
        key="signal",
        translation_key="signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("rssi"),
    ),
    TMeterSensorDescription(
        key="raw",
        translation_key="raw",
        icon="mdi:ruler",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("ullage_raw"),
    ),
    TMeterSensorDescription(
        key="flag",
        translation_key="flag",
        icon="mdi:flag",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("flag"),
    ),
    TMeterSensorDescription(
        key="last_report",
        translation_key="last_report",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _ts(d.get("last_report")),
    ),
)

CUMULATIVE_SENSORS: tuple[TMeterSensorDescription, ...] = (
    TMeterSensorDescription(
        key="energy_consumed",
        translation_key="energy_consumed",
        icon="mdi:fire",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        cumulative=True,
        value_fn=lambda d: d.get("energy_kwh"),
    ),
    TMeterSensorDescription(
        key="oil_consumed",
        translation_key="oil_consumed",
        icon="mdi:counter",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        cumulative=True,
        value_fn=lambda d: d.get("consumed_gal"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors, creating entities per device as they are discovered."""
    coordinator: TMeterDataCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    added: set[str] = set()

    @callback
    def _add(device_id: str) -> None:
        if device_id in added:
            return
        added.add(device_id)
        entities: list[SensorEntity] = [
            TMeterSensor(coordinator, device_id, desc) for desc in SENSORS
        ]
        entities += [
            TMeterCumulativeSensor(coordinator, device_id, desc)
            for desc in CUMULATIVE_SENSORS
        ]
        async_add_entities(entities)

    for device_id in list(coordinator.data):
        _add(device_id)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_DEVICE.format(entry_id=entry.entry_id), _add
        )
    )


class _TMeterBase(CoordinatorEntity[TMeterDataCoordinator]):
    """Shared device/availability plumbing keyed by device id."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: TMeterSensorDescription

    def __init__(
        self,
        coordinator: TMeterDataCoordinator,
        device_id: str,
        description: TMeterSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def _device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._device_id, {})

    @property
    def available(self) -> bool:
        return self._device_id in self.coordinator.data

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


class TMeterSensor(_TMeterBase, SensorEntity):
    """A standard (non-cumulative) reading."""

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._device)


class TMeterCumulativeSensor(_TMeterBase, RestoreSensor):
    """A persistent total_increasing odometer (energy kWh / oil gallons).

    The coordinator persists the running total, but we also restore the last
    sensor reading so the value is present immediately on restart and long-term
    statistics never observe a reset to zero.
    """

    _restored_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._restored_value = float(last.native_value)
            except (TypeError, ValueError):
                self._restored_value = None

    @property
    def available(self) -> bool:
        # Stay available across the sleep gap so the odometer holds its value.
        return True

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self._device)
        if value is None:
            return self._restored_value
        return value
