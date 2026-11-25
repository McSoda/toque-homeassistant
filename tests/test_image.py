"""Image de la recette en cours."""

from __future__ import annotations

import copy
from datetime import timedelta

from homeassistant.components.image import async_get_image
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.toque.api import SseEvent
from custom_components.toque.coordinator import ToqueCoordinator

from .fake_server import STATE, FakeToque
from .test_integration import setup_robot, state_of, wait_for


def idle(robot: FakeToque) -> None:
    """Recette en cours sans programme : le robot accepte de servir l'image."""
    robot.state = {**copy.deepcopy(STATE), "job": None}


def image_state(hass: HomeAssistant) -> str:
    return state_of(hass, "image", "recipe_image").state


async def content(hass: HomeAssistant) -> bytes:
    entity_id = state_of(hass, "image", "recipe_image").entity_id
    return (await async_get_image(hass, entity_id)).content


async def shown(hass: HomeAssistant) -> None:
    await wait_for(hass, lambda: image_state(hass) not in (STATE_UNKNOWN, STATE_UNAVAILABLE))


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=False)
    for _ in range(3):
        await hass.async_block_till_done()


async def later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def calls(robot: FakeToque, suffix: str) -> int:
    return sum(1 for path, _ in robot.requests if path.endswith(suffix))


async def catalogue_changed(hass: HomeAssistant, coordinator: ToqueCoordinator, rev: int) -> None:
    await coordinator._handle_event(SseEvent("recipes", {"rev": rev, "total": 312}))
    await hass.async_block_till_done()


async def test_image_of_current_recipe(hass: HomeAssistant, robot: FakeToque) -> None:
    idle(robot)
    entry = await setup_robot(hass, robot)
    await shown(hass)
    entity_id = state_of(hass, "image", "recipe_image").entity_id
    image = await async_get_image(hass, entity_id)
    assert (image.content, image.content_type) == (robot.image, "image/jpeg")

    # pas de demande en continu : les mises à jour suivantes ne redemandent rien
    coordinator: ToqueCoordinator = entry.runtime_data
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await settle(hass)
    assert robot.image_statuses == [200]
    assert calls(robot, "/recipes/veloute-de-potiron") == 1


async def test_no_image_without_recipe(hass: HomeAssistant, robot: FakeToque) -> None:
    idle(robot)
    robot.session = {"active": False}
    await setup_robot(hass, robot)
    await settle(hass)
    assert image_state(hass) == STATE_UNKNOWN
    assert calls(robot, "/image") == 0
    with pytest.raises(HomeAssistantError):
        await content(hass)


async def test_no_request_during_job(hass: HomeAssistant, robot: FakeToque) -> None:
    entry = await setup_robot(hass, robot)  # état par défaut : programme en cours
    await settle(hass)
    assert image_state(hass) == STATE_UNKNOWN
    assert calls(robot, "/recipes/veloute-de-potiron") == 0 and calls(robot, "/image") == 0

    idle(robot)  # fin du programme
    await entry.runtime_data.async_refresh()
    await shown(hass)
    assert await content(hass) == robot.image


async def test_busy_robot_is_retried_after_delay(hass: HomeAssistant, robot: FakeToque) -> None:
    idle(robot)
    robot.image_busy = 1
    entry = await setup_robot(hass, robot)
    await wait_for(hass, lambda: robot.image_statuses == [503])
    await settle(hass)
    await entry.runtime_data.async_refresh()  # avant la fin du délai : rien
    await settle(hass)
    assert robot.image_statuses == [503]

    await later(hass, 6)
    await shown(hass)
    assert robot.image_statuses == [503, 200]


async def test_missing_image_waits_for_catalogue_change(
    hass: HomeAssistant, robot: FakeToque
) -> None:
    idle(robot)
    robot.recipe["imageUrl"] = None
    robot.image = None
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    await wait_for(hass, lambda: calls(robot, "/recipes/veloute-de-potiron") == 1)
    await settle(hass)
    await coordinator.async_refresh()
    await settle(hass)
    assert image_state(hass) == STATE_UNKNOWN
    assert calls(robot, "/recipes/veloute-de-potiron") == 1 and calls(robot, "/image") == 0

    # le robot a mis l'image en cache et le signale
    robot.recipe["imageUrl"] = "/api/v1/recipes/veloute-de-potiron/image"
    robot.image = b"\xff\xd8nouvelle"
    await catalogue_changed(hass, coordinator, 8)
    await later(hass, 61)
    await shown(hass)
    assert await content(hass) == robot.image


async def test_image_404_is_unknown(hass: HomeAssistant, robot: FakeToque) -> None:
    idle(robot)
    robot.image = None  # `imageUrl` annoncée mais fichier disparu
    await setup_robot(hass, robot)
    await wait_for(hass, lambda: robot.image_statuses == [404])
    await settle(hass)
    assert image_state(hass) == STATE_UNKNOWN
    assert robot.image_statuses == [404]


async def test_etag_is_respected(hass: HomeAssistant, robot: FakeToque) -> None:
    idle(robot)
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    await shown(hass)
    first = image_state(hass)

    # catalogue modifié, image inchangée : 304, l'entité ne change pas
    await catalogue_changed(hass, coordinator, 8)
    assert robot.image_statuses == [200]  # pas avant le délai minimal
    await later(hass, 61)
    await wait_for(hass, lambda: robot.image_statuses == [200, 304])
    await settle(hass)
    assert image_state(hass) == first
    assert await content(hass) == robot.image

    # image remplacée sur le robot
    robot.image, robot.image_etag = b"\xff\xd8autre", '"7-1763000999"'
    await catalogue_changed(hass, coordinator, 9)
    await later(hass, 130)
    await wait_for(hass, lambda: image_state(hass) != first)
    assert await content(hass) == robot.image
    assert robot.image_statuses == [200, 304, 200]


async def test_session_end_and_restart_revalidates(
    hass: HomeAssistant, robot: FakeToque
) -> None:
    idle(robot)
    session = copy.deepcopy(robot.session)
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    await shown(hass)

    await coordinator._handle_event(SseEvent("session", {"active": False}))
    await hass.async_block_till_done()
    assert image_state(hass) == STATE_UNKNOWN
    with pytest.raises(HomeAssistantError):
        await content(hass)

    # même recette relancée : revalidation par ETag, aucun nouveau transfert
    await coordinator._handle_event(SseEvent("session", session))
    await shown(hass)
    assert robot.image_statuses == [200, 304]
    assert await content(hass) == robot.image


async def test_other_recipe_clears_previous_image(
    hass: HomeAssistant, robot: FakeToque
) -> None:
    idle(robot)
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    await shown(hass)
    other = copy.deepcopy(robot.session)
    other["recipe"] = {"id": "inconnue", "title": "Autre"}
    await coordinator._handle_event(SseEvent("session", other))
    await settle(hass)
    assert image_state(hass) == STATE_UNKNOWN
