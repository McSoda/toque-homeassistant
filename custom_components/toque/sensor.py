"""Capteurs Toque."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfMass,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ToqueConfigEntry, ToqueCoordinator
from .entity import ToqueEntity, dig

PARALLEL_UPDATES = 0
STATE_MAX = 255

STATUSES = ["standby", "running", "recipe", "fault"]
STEP_KINDS = ["manual", "machine"]
AWAITING = ["user", "machine"]
# Clés de `StepProgram` côté robot (les cinq dernières : Monsieur Cuisine Smart).
STEP_PROGRAMS = [
    "manuel",
    "petrir",
    "vapeur",
    "rissoler",
    "turbo",
    "mixer",
    "smoothie",
    "sous-vide",
    "mijoter",
    "fermenter",
]
# `/state` ne publie un travail que tant qu'il n'est pas fini : `finished` et `aborted`
# arrivent par l'événement `job_done` ; ils ne sont listés que par tolérance.
JOB_STATES = ["starting", "running", "stopping", "paused", "finished", "aborted"]


def _truncate(text: Any) -> str | None:
    if not text:
        return None
    text = str(text)
    return text if len(text) <= STATE_MAX else text[: STATE_MAX - 1] + "…"


def _temperature(c: ToqueCoordinator) -> Any:
    """Température mesurée ; inconnue quand la base renvoie 0.

    Le SKMC 1200 renvoie 0 tant que le contenu est sous 37 °C (plancher de la sonde) :
    ce n'est pas une mesure, afficher « 0 °C » pour une eau froide serait trompeur.
    """
    value = dig(c.state, "machine", "tempC")
    return None if value == 0 else value


def _lower(value: Any) -> str | None:
    """Valeur d'énumération en minuscules ; None si absente ou vide."""
    return str(value).lower() if value else None


def _option(value: Any, options: list[str]) -> str | None:
    """Valeur d'un capteur énuméré ; inconnue si le robot en annonce une nouvelle."""
    return value if value in options else None


def _step_setting(c: ToqueCoordinator, key: str) -> Any:
    """Réglage de la recette pour l'étape courante (pas celui adopté sur le robot)."""
    return dig(c.session, "step", "machine", key)


def _step_program_attributes(c: ToqueCoordinator) -> dict[str, Any] | None:
    if not dig(c.session, "step", "machine"):
        return None
    return {
        "reverse": _step_setting(c, "reverse"),
        "accessory": _lower(_step_setting(c, "accessory")),
        "pulses": _step_setting(c, "pulses"),
    }


def _step_weight_attributes(c: ToqueCoordinator) -> dict[str, Any] | None:
    if not dig(c.session, "step", "weigh"):
        return None
    return {"label": dig(c.session, "step", "weigh", "label")}


def _step_number_attributes(c: ToqueCoordinator) -> dict[str, Any] | None:
    if not c.session.get("active"):
        return None
    # Index à partir de 0, comme dans l'API.
    return {"done_steps": c.session.get("doneSteps")}


def _job_attributes(c: ToqueCoordinator) -> dict[str, Any] | None:
    job = c.state.get("job")
    if not job:
        return None
    return {
        "kind": _lower(job.get("kind")),
        "label": job.get("label"),
        "pause_reason": _lower(job.get("pauseReason")),
        "phase": job.get("phase"),
        "phase_count": job.get("phaseCount"),
        "reverse": job.get("reverse"),
        "accessory": _lower(job.get("accessory")),
    }


def _step_count(c: ToqueCoordinator) -> str | None:
    s = c.session
    if not s.get("active") or s.get("stepIndex") is None or not s.get("stepCount"):
        return None
    return f"{s['stepIndex'] + 1}/{s['stepCount']}"


@dataclass(frozen=True, kw_only=True)
class ToqueSensorDescription(SensorEntityDescription):
    """Description d'un capteur : fonction de lecture et dépendance à la fraîcheur."""

    value_fn: Callable[[ToqueCoordinator], Any]
    needs_fresh: bool = False
    # Texte complet en attribut quand l'état est tronqué à 255 caractères.
    full_text_fn: Callable[[ToqueCoordinator], Any] | None = None
    attributes_fn: Callable[[ToqueCoordinator], dict[str, Any] | None] | None = None


def _manual_text(c: ToqueCoordinator, key: str) -> Any:
    return dig(c.session, key, "text")


SENSORS: tuple[ToqueSensorDescription, ...] = (
    ToqueSensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        value_fn=lambda c: c.state.get("status"),
    ),
    ToqueSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        needs_fresh=True,
        value_fn=_temperature,
    ),
    ToqueSensorDescription(
        key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        needs_fresh=True,
        value_fn=lambda c: dig(c.state, "machine", "targetTempC"),
    ),
    ToqueSensorDescription(
        key="rpm",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        needs_fresh=True,
        icon="mdi:rotate-right",
        value_fn=lambda c: dig(c.state, "machine", "rpm"),
    ),
    ToqueSensorDescription(
        key="speed_level",
        state_class=SensorStateClass.MEASUREMENT,
        needs_fresh=True,
        icon="mdi:speedometer",
        # `targetSpeed` : niveau reçu par la base ; `rpm`/`speed` restent à 0 en marche.
        value_fn=lambda c: dig(c.state, "machine", "targetSpeed"),
    ),
    ToqueSensorDescription(
        key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        needs_fresh=True,
        # null tant que le moteur tourne (la base renvoie alors 0).
        value_fn=lambda c: dig(c.state, "machine", "weightG"),
    ),
    ToqueSensorDescription(
        key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        needs_fresh=True,
        value_fn=lambda c: dig(c.state, "job", "remainingS"),
    ),
    ToqueSensorDescription(
        key="expected_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        needs_fresh=True,
        value_fn=lambda c: c.job_end,
    ),
    ToqueSensorDescription(
        key="job_state",
        device_class=SensorDeviceClass.ENUM,
        options=JOB_STATES,
        needs_fresh=True,
        icon="mdi:progress-clock",
        value_fn=lambda c: _option(_lower(dig(c.state, "job", "state")), JOB_STATES),
        attributes_fn=_job_attributes,
    ),
    ToqueSensorDescription(
        key="job_elapsed_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        needs_fresh=True,
        value_fn=lambda c: dig(c.state, "job", "elapsedS"),
    ),
    ToqueSensorDescription(
        key="job_total_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        needs_fresh=True,
        value_fn=lambda c: dig(c.state, "job", "totalS"),
    ),
    ToqueSensorDescription(
        key="recipe",
        icon="mdi:chef-hat",
        value_fn=lambda c: dig(c.session, "recipe", "title") if c.session.get("active") else None,
    ),
    ToqueSensorDescription(
        key="step_number",
        icon="mdi:stairs",
        value_fn=_step_count,
        attributes_fn=_step_number_attributes,
    ),
    ToqueSensorDescription(
        key="step_kind",
        device_class=SensorDeviceClass.ENUM,
        options=STEP_KINDS,
        icon="mdi:gesture-tap",
        value_fn=lambda c: _option(dig(c.session, "step", "kind"), STEP_KINDS),
    ),
    ToqueSensorDescription(
        key="awaiting",
        device_class=SensorDeviceClass.ENUM,
        options=AWAITING,
        icon="mdi:timer-sand",
        value_fn=lambda c: _option(c.session.get("awaiting"), AWAITING),
    ),
    ToqueSensorDescription(
        key="step_program",
        device_class=SensorDeviceClass.ENUM,
        options=STEP_PROGRAMS,
        icon="mdi:tune",
        value_fn=lambda c: _option(_step_setting(c, "program"), STEP_PROGRAMS),
        attributes_fn=_step_program_attributes,
    ),
    ToqueSensorDescription(
        key="step_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda c: _step_setting(c, "seconds"),
    ),
    ToqueSensorDescription(
        key="step_speed",
        icon="mdi:speedometer-medium",
        value_fn=lambda c: _step_setting(c, "speed"),
    ),
    ToqueSensorDescription(
        key="step_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # Réglage de la recette : 0 = sans chauffe (ce n'est pas une mesure).
        value_fn=lambda c: _step_setting(c, "tempC"),
    ),
    ToqueSensorDescription(
        key="step_weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        icon="mdi:scale",
        value_fn=lambda c: dig(c.session, "step", "weigh", "grams"),
        attributes_fn=_step_weight_attributes,
    ),
    ToqueSensorDescription(
        key="current_step",
        icon="mdi:text-box-outline",
        value_fn=lambda c: _truncate(dig(c.session, "step", "text")),
        full_text_fn=lambda c: dig(c.session, "step", "text"),
    ),
    ToqueSensorDescription(
        key="last_manual_step",
        icon="mdi:text-box-check-outline",
        value_fn=lambda c: _truncate(_manual_text(c, "lastManual")),
        full_text_fn=lambda c: _manual_text(c, "lastManual"),
    ),
    ToqueSensorDescription(
        key="next_manual_step",
        icon="mdi:text-box-plus-outline",
        value_fn=lambda c: _truncate(_manual_text(c, "nextManual")),
        full_text_fn=lambda c: _manual_text(c, "nextManual"),
    ),
    # Diagnostic, inactifs par défaut
    ToqueSensorDescription(
        key="recipe_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:book-open-variant",
        value_fn=lambda c: c.info.get("recipeCount"),
    ),
    ToqueSensorDescription(
        key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.info.get("uptimeS"),
    ),
    ToqueSensorDescription(
        key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:ip-network",
        value_fn=lambda c: c.info.get("ip"),
    ),
    ToqueSensorDescription(
        key="android_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:android",
        value_fn=lambda c: dig(c.info, "android", "release"),
    ),
    ToqueSensorDescription(
        key="board_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:chip",
        value_fn=lambda c: dig(c.info, "board", "firmware"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ToqueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ToqueSensor(coordinator, d) for d in SENSORS)


class ToqueSensor(ToqueEntity, SensorEntity):
    """Capteur générique piloté par une description."""

    entity_description: ToqueSensorDescription
    # Ingrédients, textes longs et listes n'ont pas à encombrer la base de l'enregistreur.
    _unrecorded_attributes = frozenset({"ingredients", "text", "done_steps"})

    def __init__(self, coordinator: ToqueCoordinator, description: ToqueSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.needs_fresh)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (attributes := self.entity_description.attributes_fn) is not None:
            return attributes(self.coordinator)
        fn = self.entity_description.full_text_fn
        if fn is not None:
            text = fn(self.coordinator)
            return {"text": text} if text else None
        if self.entity_description.key == "recipe" and self.coordinator.session.get("active"):
            s = self.coordinator.session
            return {
                "recipe_id": dig(s, "recipe", "id"),
                "category": dig(s, "recipe", "category"),
                "servings": dig(s, "recipe", "servings"),
                "ingredients": s.get("ingredients"),
            }
        return None
