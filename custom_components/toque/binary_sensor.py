"""Capteurs binaires Toque."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity, dig

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ToqueBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[ToqueCoordinator], bool | None]
    needs_fresh: bool = False


def _flag(*keys: str) -> Callable[[ToqueCoordinator], bool | None]:
    def read(c: ToqueCoordinator) -> bool | None:
        value = dig(c.state, *keys)
        return None if value is None else bool(value)

    return read


BINARY_SENSORS: tuple[ToqueBinarySensorDescription, ...] = (
    ToqueBinarySensorDescription(
        key="lid_open",
        device_class=BinarySensorDeviceClass.OPENING,
        needs_fresh=True,
        value_fn=_flag("machine", "lidOpen"),
    ),
    ToqueBinarySensorDescription(
        key="bowl_present",
        needs_fresh=True,
        icon="mdi:pot-mix",
        value_fn=_flag("machine", "bowlDetected"),
    ),
    ToqueBinarySensorDescription(
        key="motor",
        device_class=BinarySensorDeviceClass.RUNNING,
        needs_fresh=True,
        value_fn=_flag("machine", "motorOn"),
    ),
    ToqueBinarySensorDescription(
        key="heater",
        device_class=BinarySensorDeviceClass.HEAT,
        needs_fresh=True,
        value_fn=_flag("machine", "heaterOn"),
    ),
    ToqueBinarySensorDescription(
        key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: c.state.get("fault") is not None if c.state else None,
    ),
    ToqueBinarySensorDescription(
        key="screen_on",
        icon="mdi:monitor",
        value_fn=_flag("screenOn"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ToqueBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class ToqueBinarySensor(ToqueEntity, BinarySensorEntity):
    entity_description: ToqueBinarySensorDescription

    def __init__(
        self, coordinator: ToqueCoordinator, description: ToqueBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key, description.needs_fresh)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self.entity_description.key == "fault":
            fault = self.coordinator.state.get("fault")
            if fault:
                return {"code": fault.get("code"), "message": fault.get("message")}
        return None
