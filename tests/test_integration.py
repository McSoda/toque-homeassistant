"""Tests de l'intégration complète contre le faux robot."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.toque.const import CONF_TOKEN, DOMAIN
from custom_components.toque.coordinator import ToqueCoordinator

from .fake_server import STATE, TOKEN, FakeToque, sse


async def setup_robot(hass: HomeAssistant, robot: FakeToque, token: str = TOKEN) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=robot.info["id"],
        data={CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: token},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def state_of(hass: HomeAssistant, domain: str, key: str):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, f"toque-test0001_{key}")
    assert entity_id, key
    return hass.states.get(entity_id)


async def wait_for(hass: HomeAssistant, predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
    raise AssertionError("condition jamais remplie")


async def test_entities_from_state_and_session(hass: HomeAssistant, robot: FakeToque) -> None:
    entry = await setup_robot(hass, robot)
    assert entry.state is ConfigEntryState.LOADED

    assert state_of(hass, "sensor", "status").state == "recipe"
    assert state_of(hass, "sensor", "temperature").state == "82"
    assert state_of(hass, "sensor", "target_temperature").state == "90"
    assert state_of(hass, "sensor", "speed_level").state == "4"
    # poids null moteur en marche
    assert state_of(hass, "sensor", "weight").state == STATE_UNKNOWN
    assert float(state_of(hass, "sensor", "remaining_time").state) == pytest.approx(260 / 60, abs=0.01)
    assert state_of(hass, "binary_sensor", "lid_open").state == "off"
    assert state_of(hass, "binary_sensor", "bowl_present").state == "on"
    assert state_of(hass, "binary_sensor", "motor").state == "on"
    assert state_of(hass, "binary_sensor", "heater").state == "on"
    assert state_of(hass, "binary_sensor", "fault").state == "off"
    assert state_of(hass, "binary_sensor", "screen_on").state == "on"

    assert state_of(hass, "sensor", "recipe").state == "Velouté de potiron"
    assert state_of(hass, "sensor", "recipe").attributes["ingredients"][0]["items"][0]["name"] == "potiron"
    assert state_of(hass, "sensor", "step_number").state == "3/8"
    assert state_of(hass, "sensor", "current_step").state == "Ajouter la crème."
    assert state_of(hass, "sensor", "last_manual_step").state == "Éplucher et couper le potiron."

    # texte long : état tronqué à 255 caractères, texte complet en attribut
    nxt = state_of(hass, "sensor", "next_manual_step")
    assert len(nxt.state) == 255 and nxt.state.endswith("…")
    assert nxt.attributes["text"] == "x" * 300


async def test_device_info(hass: HomeAssistant, robot: FakeToque) -> None:
    from homeassistant.helpers import device_registry as dr

    entry = await setup_robot(hass, robot)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "toque-test0001"), entry.entry_id)
    assert device.sw_version == "0.5.5"
    assert device.model == "SKMC 1200"
    assert device.hw_version == "4922"


async def test_no_session_no_job(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state = {**copy.deepcopy(STATE), "status": "standby", "job": None, "sessionId": None}
    robot.state["machine"]["weightG"] = 120
    robot.session = {"active": False}
    await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "status").state == "standby"
    assert state_of(hass, "sensor", "weight").state == "120"
    assert state_of(hass, "sensor", "remaining_time").state == STATE_UNKNOWN
    assert state_of(hass, "sensor", "recipe").state == STATE_UNKNOWN
    assert state_of(hass, "sensor", "step_number").state == STATE_UNKNOWN


async def test_stale_snapshot_makes_machine_entities_unavailable(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state["snapshotAgeMs"] = 6000
    await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "temperature").state == STATE_UNAVAILABLE
    assert state_of(hass, "binary_sensor", "motor").state == STATE_UNAVAILABLE
    # le statut et la session ne dépendent pas de la fraîcheur de la base moteur
    assert state_of(hass, "sensor", "status").state == "recipe"
    assert state_of(hass, "sensor", "recipe").state == "Velouté de potiron"


async def test_link_offline_makes_machine_entities_unavailable(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state["link"] = "OFFLINE"
    await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "temperature").state == STATE_UNAVAILABLE


async def test_snapshot_age_minus_one_is_not_fresh(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state["snapshotAgeMs"] = -1
    await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "temperature").state == STATE_UNAVAILABLE


async def test_robot_unavailable_after_two_heartbeats(hass: HomeAssistant, robot: FakeToque) -> None:
    # SSE ouvert mais silencieux après l'envoi initial
    robot.event_scripts = [[sse("state", robot.state), None]]
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    await wait_for(hass, lambda: coordinator.stream_connected)

    now = [1000.0]
    coordinator._clock = lambda: now[0]
    coordinator._last_state_at = now[0]
    coordinator._check_availability()
    assert state_of(hass, "sensor", "status").state == "recipe"

    now[0] += 9  # job en cours : fenêtre de 2 x 5 s = 10 s
    assert coordinator.robot_available
    now[0] += 2
    coordinator._check_availability()
    await hass.async_block_till_done()
    assert not coordinator.robot_available
    assert state_of(hass, "sensor", "status").state == STATE_UNAVAILABLE
    assert state_of(hass, "sensor", "recipe").state == STATE_UNAVAILABLE

    # un nouveau `state` rétablit le robot
    coordinator._apply_state(robot.state)
    coordinator.async_set_updated_data(coordinator._snapshot())
    await hass.async_block_till_done()
    assert state_of(hass, "sensor", "status").state == "recipe"


async def test_idle_window_is_sixty_seconds(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state["job"] = None
    robot.event_scripts = [[sse("state", robot.state), None]]
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    await wait_for(hass, lambda: coordinator.stream_connected)
    now = [0.0]
    coordinator._clock = lambda: now[0]
    coordinator._last_state_at = 0.0
    now[0] = 59
    assert coordinator.robot_available
    now[0] = 61
    assert not coordinator.robot_available


async def test_sse_state_update_and_cooking_events(hass: HomeAssistant, robot: FakeToque) -> None:
    updated = copy.deepcopy(robot.state)
    updated["machine"]["tempC"] = 95
    updated["fault"] = {"code": "E1", "message": "Surchauffe"}
    waiting = {**copy.deepcopy(robot.session), "awaiting": "machine", "stepIndex": 3}
    user_again = {**copy.deepcopy(robot.session), "awaiting": "user", "stepIndex": 4}
    robot.event_scripts = [
        [
            sse("state", robot.state),
            sse("session", waiting),
            sse("state", updated, 1),
            sse("step", {"sessionId": "c1d2", "index": 4, "kind": "manual", "text": "Mixer"}, 2),
            sse("session", user_again, 3),
            sse("job_done", {"kind": "MANUAL", "label": "Étape 3", "result": "finished", "reason": "", "totalS": 300}, 4),
            sse("recipes", {"rev": 8, "total": 315}, 5),
            sse("inconnu", {"x": 1}, 6),
            None,
        ]
    ]
    entry = await setup_robot(hass, robot)
    await wait_for(hass, lambda: state_of(hass, "sensor", "temperature").state == "95")

    assert state_of(hass, "binary_sensor", "fault").state == "on"
    assert state_of(hass, "binary_sensor", "fault").attributes["code"] == "E1"

    coordinator: ToqueCoordinator = entry.runtime_data
    await wait_for(hass, lambda: coordinator.info.get("recipeCount") == 315)
    assert coordinator.stream.last_event_id == "6"

    event = state_of(hass, "event", "cooking")
    # dernier événement reçu : job_done (action_requise et step l'ont précédé)
    assert event.attributes["event_type"] == "job_done"
    assert event.attributes["result"] == "finished"
    assert set(event.attributes["event_types"]) == {"step", "action_requise", "done", "job_done", "fault"}


async def test_action_required_event_is_derived(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.session["awaiting"] = "machine"
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    seen: list[str] = []
    coordinator.async_add_event_listener(lambda t, d: seen.append(t))
    await coordinator._handle_event(_event("session", {**robot.session, "awaiting": "user"}))
    await coordinator._handle_event(_event("session", {**robot.session, "awaiting": "user"}))  # pas de doublon
    assert seen == ["action_requise"]


def _event(name: str, data: dict):
    from custom_components.toque.api import SseEvent

    return SseEvent(name, data)


async def test_bad_token_starts_reauth(hass: HomeAssistant, robot: FakeToque) -> None:
    entry = await setup_robot(hass, robot, token="mauvais")
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_unreachable_robot_retries(hass: HomeAssistant, robot: FakeToque) -> None:
    await robot.stop()
    entry = await setup_robot(hass, robot)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(hass: HomeAssistant, robot: FakeToque) -> None:
    entry = await setup_robot(hass, robot)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_services(hass: HomeAssistant, robot: FakeToque) -> None:
    await setup_robot(hass, robot)
    listed = await hass.services.async_call(
        DOMAIN, "list_recipes", {"query": "velouté", "limit": 5}, blocking=True, return_response=True
    )
    assert listed["total"] == 1
    assert "q=vel" in robot.requests[-1][0] and "limit=5" in robot.requests[-1][0]

    recipe = await hass.services.async_call(
        DOMAIN, "get_recipe", {"recipe_id": "veloute-de-potiron"}, blocking=True, return_response=True
    )
    assert recipe["title"] == "Velouté de potiron"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "get_recipe", {"recipe_id": "inconnue"}, blocking=True, return_response=True
        )


async def test_add_recipe_to_todo(hass: HomeAssistant, robot: FakeToque) -> None:
    await setup_robot(hass, robot)
    calls = async_mock_service(hass, "todo", "add_item")
    await hass.services.async_call(
        DOMAIN,
        "add_recipe_to_todo",
        {"recipe_id": "veloute-de-potiron", "todo_entity_id": "todo.courses"},
        blocking=True,
    )
    assert [c.data["item"] for c in calls] == ["potiron (800 g)", "sel"]
    assert all(c.data["entity_id"] == "todo.courses" for c in calls)
