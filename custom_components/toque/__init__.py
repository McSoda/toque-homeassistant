"""Intégration Home Assistant pour Toque (robot Monsieur Cuisine Connect)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ToqueClient, ToqueError, ToqueNotFoundError
from .const import (
    CONF_TOKEN,
    DOMAIN,
    SERVICE_ADD_TO_TODO,
    SERVICE_GET_RECIPE,
    SERVICE_LIST_RECIPES,
)
from .coordinator import ToqueConfigEntry, ToqueCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_ENTRY = {vol.Optional("config_entry_id"): cv.string}
LIST_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Optional("query"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("favorite"): cv.boolean,
        vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
        vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
GET_SCHEMA = vol.Schema({**_ENTRY, vol.Required("recipe_id"): cv.string})
TODO_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required("recipe_id"): cv.string,
        vol.Required("todo_entity_id"): cv.entity_id,
    }
)


def ingredient_lines(recipe: dict[str, Any]) -> list[str]:
    """Aplatit les ingrédients (groupés `[{group, items:[…]}]` ou à plat) en lignes de texte."""
    lines: list[str] = []

    def add(item: dict[str, Any]) -> None:
        parts = [str(item.get("qty") or "").strip(), str(item.get("unit") or "").strip()]
        name = str(item.get("name") or "").strip()
        label = " ".join(p for p in parts if p)
        text = f"{name} ({label})" if label and name else (name or label)
        if text:
            lines.append(text)

    for entry in recipe.get("ingredients") or []:
        if isinstance(entry, dict) and "items" in entry:
            for item in entry.get("items") or []:
                if isinstance(item, dict):
                    add(item)
        elif isinstance(entry, dict):
            add(entry)
    return lines


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> ToqueCoordinator:
    entries = [
        e for e in hass.config_entries.async_loaded_entries(DOMAIN) if hasattr(e, "runtime_data")
    ]
    wanted = call.data.get("config_entry_id")
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if len(entries) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found" if not entries else "entry_ambiguous",
        )
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Enregistre les services (une seule fois pour tous les robots)."""

    async def list_recipes(call: ServiceCall) -> dict[str, Any]:
        client = _coordinator(hass, call).client
        try:
            return await client.get_recipes(
                q=call.data.get("query"),
                category=call.data.get("category"),
                favorite=1 if call.data.get("favorite") else None,
                limit=call.data["limit"],
                offset=call.data["offset"],
            )
        except ToqueError as err:
            raise ServiceValidationError(str(err)) from err

    async def get_recipe(call: ServiceCall) -> dict[str, Any]:
        client = _coordinator(hass, call).client
        try:
            return await client.get_recipe(call.data["recipe_id"])
        except ToqueNotFoundError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="recipe_not_found",
                translation_placeholders={"recipe_id": call.data["recipe_id"]},
            ) from err
        except ToqueError as err:
            raise ServiceValidationError(str(err)) from err

    async def add_to_todo(call: ServiceCall) -> None:
        client = _coordinator(hass, call).client
        try:
            recipe = await client.get_recipe(call.data["recipe_id"])
        except ToqueNotFoundError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="recipe_not_found",
                translation_placeholders={"recipe_id": call.data["recipe_id"]},
            ) from err
        except ToqueError as err:
            raise ServiceValidationError(str(err)) from err
        for line in ingredient_lines(recipe):
            await hass.services.async_call(
                "todo",
                "add_item",
                {"entity_id": call.data["todo_entity_id"], "item": line},
                blocking=True,
            )

    hass.services.async_register(
        DOMAIN, SERVICE_LIST_RECIPES, list_recipes, LIST_SCHEMA, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_RECIPE, get_recipe, GET_SCHEMA, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(DOMAIN, SERVICE_ADD_TO_TODO, add_to_todo, TODO_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ToqueConfigEntry) -> bool:
    client = ToqueClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    try:
        info = await client.get_info()
    except ToqueError as err:
        raise ConfigEntryNotReady(f"Robot injoignable: {err}") from err

    coordinator = ToqueCoordinator(hass, entry, client, info)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ToqueConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
