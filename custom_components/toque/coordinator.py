"""Coordinateur : SSE en temps réel, interrogation de /state en repli."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SseEvent,
    ToqueAuthError,
    ToqueClient,
    ToqueError,
    ToqueEventStream,
)
from .const import (
    AVAILABILITY_CHECK_S,
    DOMAIN,
    HEARTBEAT_IDLE_S,
    HEARTBEAT_JOB_S,
    JOB_END_TOLERANCE_S,
    JOB_RUNNING_STATES,
    MAX_SNAPSHOT_AGE_MS,
    POLL_INTERVAL_S,
    SILENCE_GRACE_S,
)

_LOGGER = logging.getLogger(__name__)

type ToqueConfigEntry = ConfigEntry[ToqueCoordinator]


class ToqueCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reçoit l'état du robot et décide de sa disponibilité."""

    config_entry: ToqueConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ToqueConfigEntry,
        client: ToqueClient,
        info: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL_S),
        )
        self.client = client
        self.info = info
        self._clock = clock
        self.state: dict[str, Any] = {}
        self.session: dict[str, Any] = {"active": False}
        # Fin prévue du travail en cours, figée tant que les estimations concordent.
        self.job_end: datetime | None = None
        self._utcnow: Callable[[], datetime] = dt_util.utcnow
        self._last_state_at: float | None = None
        self.stream_connected = False
        self._poll_task: asyncio.Task | None = None
        self._event_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._last_availability: tuple[bool, bool] | None = None
        self.stream = ToqueEventStream(
            client,
            self._handle_event,
            on_connection_change=self._handle_connection,
            on_auth_error=self._handle_auth_error,
        )

    # --- cycle de vie -------------------------------------------------

    async def async_start(self) -> None:
        """Démarre le flux SSE et la surveillance de disponibilité."""
        self.stream.start()
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._check_availability,
                timedelta(seconds=AVAILABILITY_CHECK_S),
            )
        )
        self.config_entry.async_on_unload(self.stream.stop)
        self.config_entry.async_on_unload(self._cancel_poll)

    @callback
    def _cancel_poll(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    async def _async_update_data(self) -> dict[str, Any]:
        """Repli : interrogation de /state et /session."""
        try:
            state = await self.client.get_state()
            session = await self.client.get_session()
        except ToqueAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ToqueError as err:
            raise UpdateFailed(str(err)) from err
        self._apply_state(state)
        # Premier chargement : pas d'événement dérivé ; ensuite le repli doit en émettre,
        # car le flux peut être muet (les doublons sont écartés par la comparaison).
        self._apply_session(session, emit=self.data is not None)
        return self._snapshot()

    # --- application des données --------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "session": self.session}

    def _apply_state(self, state: dict[str, Any]) -> None:
        self.state = state
        self._last_state_at = self._clock()
        self._update_job_end(state)

    def _update_job_end(self, state: dict[str, Any]) -> None:
        """Estime la fin du travail à la réception d'un `state` (jamais entre deux)."""
        job = state.get("job") or {}
        remaining = job.get("remainingS")
        if (
            str(job.get("state") or "").lower() not in JOB_RUNNING_STATES
            or not isinstance(remaining, (int, float))
            or remaining <= 0
        ):
            self.job_end = None
            return
        age = state.get("snapshotAgeMs")
        age_ms = age if isinstance(age, (int, float)) and age > 0 else 0
        # `remainingS` date de l'instantané de la base, pas de maintenant.
        estimate = self._utcnow() + timedelta(seconds=remaining, milliseconds=-age_ms)
        if self.job_end is None or abs(estimate - self.job_end) > timedelta(
            seconds=JOB_END_TOLERANCE_S
        ):
            self.job_end = estimate.replace(microsecond=0)

    def _apply_session(self, session: dict[str, Any], *, emit: bool) -> None:
        previous = self.session
        self.session = session
        if (
            emit
            and session.get("active")
            and session.get("awaiting") == "user"
            and (
                not previous.get("active")
                or previous.get("id") != session.get("id")
                or previous.get("awaiting") != "user"
                or previous.get("stepIndex") != session.get("stepIndex")
            )
        ):
            # Pas d'événement SSE « action requise » : on le déduit de `session`.
            self._emit(
                "action_requise",
                {
                    "sessionId": session.get("id"),
                    "index": session.get("stepIndex"),
                    "text": (session.get("step") or {}).get("text"),
                },
            )

    async def _handle_event(self, event: SseEvent) -> None:
        data = event.data if isinstance(event.data, dict) else {}
        if event.event == "state":
            self._apply_state(data)
        elif event.event == "session":
            self._apply_session(data, emit=True)
        elif event.event in ("step", "done", "job_done", "fault"):
            self._emit(event.event, data)
            return
        elif event.event == "recipes":
            total = data.get("total")
            if total is not None:
                self.info = {**self.info, "recipeCount": total, "recipesRev": data.get("rev")}
        else:
            return  # événement inconnu : ignoré (évolutions additives)
        self.async_set_updated_data(self._snapshot())

    @callback
    def _handle_connection(self, connected: bool) -> None:
        if connected != self.stream_connected:
            self.stream_connected = connected
            self._check_availability()

    @callback
    def _handle_auth_error(self) -> None:
        self.config_entry.async_start_reauth(self.hass)

    # --- événements de cuisine ------------------------------------------

    def async_add_event_listener(
        self, listener: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        for listener in list(self._event_listeners):
            listener(event_type, data)

    # --- disponibilité ----------------------------------------------------

    @property
    def robot_available(self) -> bool:
        """Faux si aucun `state` n'est reçu en 2x le signal de vie (spec §3)."""
        if self._last_state_at is None:
            return False
        if self.stream_connected:
            heartbeat = HEARTBEAT_JOB_S if self.state.get("job") else HEARTBEAT_IDLE_S
            window = 2 * heartbeat
        else:
            # Sans flux, seule l'interrogation de repli renouvelle l'état.
            window = 2 * POLL_INTERVAL_S
        return self._clock() - self._last_state_at <= window

    @property
    def data_fresh(self) -> bool:
        """Mesures exploitables : liaison avec la base et instantané récent (spec §3)."""
        if not self.robot_available:
            return False
        if self.state.get("link") == "OFFLINE":
            return False
        age = self.state.get("snapshotAgeMs")
        return isinstance(age, (int, float)) and 0 <= age <= MAX_SNAPSHOT_AGE_MS

    @callback
    def _poll_if_silent(self) -> None:
        """Interroge /state quand plus aucun `state` n'arrive (flux muet mais ouvert)."""
        if self._last_state_at is None or (self._poll_task and not self._poll_task.done()):
            return
        heartbeat = HEARTBEAT_JOB_S if self.state.get("job") else HEARTBEAT_IDLE_S
        if self._clock() - self._last_state_at > heartbeat + SILENCE_GRACE_S:
            self._poll_task = self.hass.async_create_background_task(
                self.async_refresh(), f"{DOMAIN} poll fallback"
            )

    @callback
    def _check_availability(self, _now: Any = None) -> None:
        self._poll_if_silent()
        current = (self.robot_available, self.data_fresh)
        if current != self._last_availability:
            self._last_availability = current
            self.async_update_listeners()
