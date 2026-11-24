"""Entité de base Toque."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ToqueCoordinator


def dig(data: dict[str, Any] | None, *keys: str) -> Any:
    """Lecture tolérante dans un dictionnaire imbriqué (None si absent)."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def firmware_label(firmware: Any) -> str | None:
    """Version matérielle à afficher ; None si absente ou nulle."""
    if firmware in (None, "", 0, "0"):
        return None
    return str(firmware)


class ToqueEntity(CoordinatorEntity[ToqueCoordinator]):
    """Entité rattachée à l'appareil du robot.

    ``needs_fresh`` : l'entité dépend des mesures de la base moteur et devient
    indisponible quand l'instantané n'est plus exploitable.
    """

    _attr_has_entity_name = True
    needs_fresh = False

    def __init__(self, coordinator: ToqueCoordinator, key: str, needs_fresh: bool) -> None:
        super().__init__(coordinator)
        info = coordinator.info
        self.needs_fresh = needs_fresh
        self._attr_translation_key = key
        self._attr_unique_id = f"{info['id']}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, info["id"])},
            name=info.get("name") or "Monsieur Cuisine",
            manufacturer="Lidl / Toque",
            model=info.get("model") or "SKMC 1200",
            sw_version=dig(info, "toque", "version"),
            # 0 = firmware inconnu (le robot ne le renseigne pas) : on n'affiche rien.
            hw_version=firmware_label(dig(info, "board", "firmware")),
            configuration_url=f"http://{coordinator.client.host}:{coordinator.client.port}/api/v1/info",
        )

    @property
    def available(self) -> bool:
        coordinator = self.coordinator
        if not coordinator.robot_available:
            return False
        return coordinator.data_fresh if self.needs_fresh else True

    @property
    def state_data(self) -> dict[str, Any]:
        return self.coordinator.state

    @property
    def session_data(self) -> dict[str, Any]:
        return self.coordinator.session
