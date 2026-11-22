"""Entité événement « Cuisine »."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ToqueCookingEvent(entry.runtime_data)])


class ToqueCookingEvent(ToqueEntity, EventEntity):
    """Événements de cuisine : étape, action requise, fin de recette, fin de travail, défaut."""

    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: ToqueCoordinator) -> None:
        super().__init__(coordinator, "cooking", needs_fresh=False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._on_event))

    @callback
    def _on_event(self, event_type: str, data: dict[str, Any]) -> None:
        if event_type in EVENT_TYPES:
            self._trigger_event(event_type, data)
            self.async_write_ha_state()
