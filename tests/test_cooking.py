"""Réglage de l'étape, travail en cours, fin prévue et traductions."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.toque.const import DOMAIN
from custom_components.toque.coordinator import ToqueCoordinator
from custom_components.toque.sensor import SENSORS, ToqueSensor

from .fake_server import STATE, FakeToque
from .test_integration import setup_robot, state_of

MACHINE_STEP = {
    "index": 3,
    "kind": "machine",
    "text": "Mixer la soupe.",
    "machine": {
        "program": "manuel", "seconds": 90, "speed": 8, "tempC": 0,
        "reverse": True, "accessory": "blade", "pulses": 0,
    },
    "weigh": None,
}

STEP_KEYS = ("step_program", "step_duration", "step_speed", "step_temperature")
JOB_KEYS = ("job_state", "job_elapsed_time", "job_total_time", "expected_end")
START = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


async def push(hass: HomeAssistant, coordinator: ToqueCoordinator) -> None:
    coordinator.async_set_updated_data(coordinator._snapshot())
    await hass.async_block_till_done()


async def test_job_entities(hass: HomeAssistant, robot: FakeToque) -> None:
    await setup_robot(hass, robot)
    job = state_of(hass, "sensor", "job_state")
    assert job.state == "running"
    expected = {
        "kind": "manual", "label": "Étape 3", "pause_reason": None, "phase": 1,
        "phase_count": 1, "reverse": False, "accessory": "blade",
    }
    assert {key: job.attributes[key] for key in expected} == expected
    assert state_of(hass, "sensor", "job_elapsed_time").state == "40"
    assert state_of(hass, "sensor", "job_total_time").state == "300"


async def test_paused_job(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state["job"].update(state="PAUSED", pauseReason="LID")
    await setup_robot(hass, robot)
    job = state_of(hass, "sensor", "job_state")
    assert job.state == "paused" and job.attributes["pause_reason"] == "lid"
    assert state_of(hass, "sensor", "expected_end").state == STATE_UNKNOWN


async def test_manual_step(hass: HomeAssistant, robot: FakeToque) -> None:
    await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "step_kind").state == "manual"
    assert state_of(hass, "sensor", "awaiting").state == "user"
    assert state_of(hass, "sensor", "step_number").attributes["done_steps"] == [1]
    weight = state_of(hass, "sensor", "step_weight")
    assert weight.state == "200" and weight.attributes["label"] == "Crème"
    for key in STEP_KEYS:
        assert state_of(hass, "sensor", key).state == STATE_UNKNOWN, key


async def test_machine_step_shows_setting_before_start(
    hass: HomeAssistant, robot: FakeToque
) -> None:
    robot.state = {**copy.deepcopy(STATE), "job": None}
    robot.session.update(stepIndex=3, awaiting="machine", step=copy.deepcopy(MACHINE_STEP))
    entry = await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "step_kind").state == "machine"
    assert state_of(hass, "sensor", "awaiting").state == "machine"
    program = state_of(hass, "sensor", "step_program")
    assert program.state == "manuel"
    assert program.attributes["reverse"] is True
    assert program.attributes["accessory"] == "blade"
    assert program.attributes["pulses"] == 0
    assert state_of(hass, "sensor", "step_duration").state == "90"
    assert state_of(hass, "sensor", "step_speed").state == "8"
    # 0 = réglage sans chauffe : la valeur est gardée, ce n'est pas une mesure.
    assert state_of(hass, "sensor", "step_temperature").state == "0"
    assert state_of(hass, "sensor", "step_weight").state == STATE_UNKNOWN

    # étape machine terminée : plus rien d'attendu
    robot.session.update(awaiting=None, doneSteps=[1, 3])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "sensor", "awaiting").state == STATE_UNKNOWN
    assert state_of(hass, "sensor", "step_number").attributes["done_steps"] == [1, 3]


async def test_unknown_values_are_tolerated(hass: HomeAssistant, robot: FakeToque) -> None:
    # Évolution additive de l'API : une valeur inconnue ne doit pas provoquer d'erreur.
    step = copy.deepcopy(MACHINE_STEP)
    step["machine"]["program"] = "nouveau-programme"
    robot.session.update(step=step, awaiting="autre")
    robot.state["job"]["state"] = "NOUVEL_ETAT"
    await setup_robot(hass, robot)
    assert state_of(hass, "sensor", "step_program").state == STATE_UNKNOWN
    assert state_of(hass, "sensor", "awaiting").state == STATE_UNKNOWN
    assert state_of(hass, "sensor", "job_state").state == STATE_UNKNOWN
    assert state_of(hass, "sensor", "step_duration").state == "90"


async def test_nothing_in_progress(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state = {**copy.deepcopy(STATE), "status": "standby", "job": None, "sessionId": None}
    robot.session = {"active": False}
    await setup_robot(hass, robot)
    for key in (*STEP_KEYS, *JOB_KEYS, "step_kind", "awaiting", "step_weight"):
        assert state_of(hass, "sensor", key).state == STATE_UNKNOWN, key
    assert "done_steps" not in state_of(hass, "sensor", "step_number").attributes


async def test_job_entities_need_fresh_data(hass: HomeAssistant, robot: FakeToque) -> None:
    robot.state["snapshotAgeMs"] = 6000
    await setup_robot(hass, robot)
    for key in JOB_KEYS:
        assert state_of(hass, "sensor", key).state == STATE_UNAVAILABLE, key
    # La recette ne dépend pas de la base moteur.
    assert state_of(hass, "sensor", "step_kind").state == "manual"
    assert state_of(hass, "sensor", "step_weight").state == "200"


async def test_expected_end_is_stable(hass: HomeAssistant, robot: FakeToque) -> None:
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    now = [START]
    coordinator._utcnow = lambda: now[0]
    coordinator.job_end = None

    async def receive(elapsed: float, remaining: int, state: str = "RUNNING", age: int = 0):
        now[0] = START + timedelta(seconds=elapsed)
        data = copy.deepcopy(STATE)
        data["snapshotAgeMs"] = age
        data["job"].update(remainingS=remaining, state=state)
        coordinator._apply_state(data)
        await push(hass, coordinator)
        return state_of(hass, "sensor", "expected_end").state

    assert await receive(0, 260) == "2026-11-25T12:04:20+00:00"
    # gigue (arrondi de remainingS, retard du réseau) : la valeur ne bouge pas
    assert await receive(5.9, 255) == "2026-11-25T12:04:20+00:00"
    assert await receive(12.4, 245) == "2026-11-25T12:04:20+00:00"
    # l'âge de l'instantané est déduit : même fin, même avec une mesure de 4 s
    assert await receive(24, 240, age=4000) == "2026-11-25T12:04:20+00:00"
    # durée allongée sur le robot : nouvelle estimation
    assert await receive(30, 290) == "2026-11-25T12:05:20+00:00"
    # en pause : retirée ; reprise 100 s plus tard : recalculée
    assert await receive(35, 285, "PAUSED") == STATE_UNKNOWN
    assert await receive(135, 285, "STARTING") == "2026-11-25T12:07:00+00:00"
    assert await receive(140, 282) == "2026-11-25T12:07:00+00:00"
    # plus de travail
    coordinator._apply_state({**copy.deepcopy(STATE), "job": None})
    await push(hass, coordinator)
    assert state_of(hass, "sensor", "expected_end").state == STATE_UNKNOWN


async def test_expected_end_ignores_time_without_state(
    hass: HomeAssistant, robot: FakeToque
) -> None:
    # Une simple réécriture des entités, plus tard, ne déplace pas la fin prévue.
    entry = await setup_robot(hass, robot)
    coordinator: ToqueCoordinator = entry.runtime_data
    before = state_of(hass, "sensor", "expected_end").state
    assert datetime.fromisoformat(before)
    coordinator._utcnow = lambda: datetime(2030, 1, 1, tzinfo=timezone.utc)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert state_of(hass, "sensor", "expected_end").state == before


def test_large_attributes_are_not_recorded() -> None:
    assert {"ingredients", "text", "done_steps"} <= ToqueSensor._unrecorded_attributes


async def test_every_entity_is_translated(hass: HomeAssistant, robot: FakeToque) -> None:
    root = Path(__file__).parent.parent / "custom_components" / DOMAIN
    files = [root / "strings.json", root / "translations/en.json", root / "translations/fr.json"]
    entry = await setup_robot(hass, robot)
    registered = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    for path in files:
        entity = json.loads(path.read_text(encoding="utf-8"))["entity"]
        for item in registered:
            assert entity[item.domain][item.translation_key]["name"], (path.name, item.entity_id)
        for description in SENSORS:
            if description.options:
                states = entity["sensor"][description.key]["state"]
                assert set(states) == set(description.options), (path.name, description.key)


@pytest.mark.parametrize("lang", ["fr", "en"])
async def test_attribute_translations_cover_values(lang: str) -> None:
    path = Path(__file__).parent.parent / "custom_components" / DOMAIN / f"translations/{lang}.json"
    attributes = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]["job_state"][
        "state_attributes"
    ]
    # Valeurs de `JobKind`, `PauseReason` et `Accessory` côté robot.
    assert set(attributes["kind"]["state"]) == {
        "manual", "knead", "steam", "toast", "turbo", "program",
    }
    assert set(attributes["pause_reason"]["state"]) == {"user", "lid", "safety"}
    assert set(attributes["accessory"]["state"]) == {"blade", "whisk"}
