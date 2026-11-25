"""Image de la recette en cours.

Le robot ne sert une image que s'il l'a en cache (404 sinon) et jamais pendant un programme
(503). `/session` ne donne pas `imageUrl` : on lit `/recipes/{id}`, puis `/recipes/{id}/image`.
L'image est demandée au début d'une recette et quand le catalogue change, pas en continu.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import RecipeImage, ToqueBusyError, ToqueError, ToqueNotFoundError
from .const import DOMAIN, IMAGE_BUSY_RETRY_S, IMAGE_ERROR_RETRY_S, IMAGE_REFRESH_MIN_S
from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity, dig

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ToqueRecipeImage(hass, entry.runtime_data)])


class ToqueRecipeImage(ToqueEntity, ImageEntity):
    """Image de la recette guidée en cours ; état inconnu s'il n'y en a pas à montrer."""

    def __init__(self, hass: HomeAssistant, coordinator: ToqueCoordinator) -> None:
        super().__init__(coordinator, "recipe_image", needs_fresh=False)
        ImageEntity.__init__(self, hass)
        self._recipe_id: str | None = None
        # Dernière image reçue, gardée après la fin de la recette pour la revalider par ETag.
        self._cache: tuple[str, RecipeImage] | None = None
        self._shown: RecipeImage | None = None
        # `(recipesRev,)` pour lequel le robot a déjà répondu ; None = à demander.
        self._answered: tuple[Any] | None = None
        self._not_before = 0.0
        self._fetching = False
        self._cancel_retry: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._drop_retry)
        self._evaluate()

    async def async_image(self) -> bytes | None:
        return self._shown.content if self._shown else None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._evaluate()
        super()._handle_coordinator_update()

    # --- décision ---------------------------------------------------------

    def _current_recipe(self) -> str | None:
        if not self.session_data.get("active"):
            return None
        return dig(self.session_data, "recipe", "id")

    @callback
    def _evaluate(self) -> None:
        """Demande l'image si celle affichée n'est plus à jour et que le robot le permet."""
        recipe_id = self._current_recipe()
        if recipe_id != self._recipe_id:
            self._recipe_id = recipe_id
            self._answered = None
            self._not_before = 0.0
            self._drop_retry()
            self._show(None)
        if recipe_id is None or self._fetching or not self.coordinator.robot_available:
            return
        rev = self.coordinator.info.get("recipesRev")
        if self._answered == (rev,):
            return
        if self.state_data.get("job"):
            return  # pas d'image pendant un programme : le `state` de fin relancera
        wait = self._not_before - time.monotonic()
        if wait > 0:
            self._schedule(wait)
            return
        self._fetching = True
        self.coordinator.config_entry.async_create_background_task(
            self.hass, self._fetch(recipe_id, rev), f"{DOMAIN} recipe image"
        )

    async def _fetch(self, recipe_id: str, rev: Any) -> None:
        client = self.coordinator.client
        image: RecipeImage | None = None
        delay: float | None = None
        try:
            recipe = await client.get_recipe(recipe_id)
            if recipe.get("imageUrl"):
                cached = self._cache[1] if self._cache and self._cache[0] == recipe_id else None
                fresh = await client.get_recipe_image(recipe_id, cached.etag if cached else None)
                image = fresh or cached  # None : inchangée (304)
        except ToqueNotFoundError:
            pass  # pas en cache sur le robot : on attend un changement du catalogue
        except ToqueBusyError as err:
            delay = err.retry_after or IMAGE_BUSY_RETRY_S
        except ToqueError as err:
            _LOGGER.debug("Image de la recette %s non lue: %s", recipe_id, err)
            delay = IMAGE_ERROR_RETRY_S
        finally:
            self._fetching = False

        # Une réponse pour une recette qui n'est plus la bonne est ignorée.
        if recipe_id == self._recipe_id:
            if delay is not None:
                self._not_before = time.monotonic() + delay
            else:
                self._answered = (rev,)
                self._not_before = time.monotonic() + IMAGE_REFRESH_MIN_S
                if image is not None:
                    self._cache = (recipe_id, image)
                self._show(image)
                self.async_write_ha_state()
        # Recette ou catalogue changés pendant la demande, ou nouvel essai à programmer.
        self._evaluate()

    @callback
    def _show(self, image: RecipeImage | None) -> None:
        if image is self._shown:
            return
        self._shown = image
        if image is not None:
            self._attr_content_type = image.content_type
        self._attr_image_last_updated = None if image is None else dt_util.utcnow()

    # --- nouvel essai -----------------------------------------------------

    @callback
    def _schedule(self, delay: float) -> None:
        if self._cancel_retry is None:
            self._cancel_retry = async_call_later(self.hass, delay, self._retry)

    @callback
    def _retry(self, _now: Any) -> None:
        self._cancel_retry = None
        self._not_before = 0.0
        self._evaluate()

    @callback
    def _drop_retry(self) -> None:
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None
