"""Client de l'API locale de Toque (HTTP + SSE, lecture seule)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_PREFIX

_LOGGER = logging.getLogger(__name__)


class ToqueError(Exception):
    """Erreur générique de l'API Toque."""


class ToqueConnectionError(ToqueError):
    """Le robot ne répond pas (réseau, délai, 503, réponse illisible)."""


class ToqueAuthError(ToqueError):
    """Jeton refusé (401)."""


class ToqueNotFoundError(ToqueError):
    """Ressource inconnue (404)."""


@dataclass(frozen=True)
class SseEvent:
    """Un événement SSE complet."""

    event: str
    data: Any
    id: str | None = None


class SseParser:
    """Décodeur incrémental du format text/event-stream (ligne par ligne)."""

    def __init__(self) -> None:
        self._event = "message"
        self._data: list[str] = []
        self._id: str | None = None

    def feed(self, line: str) -> SseEvent | None:
        """Consomme une ligne (sans fin de ligne) ; renvoie un événement si complet."""
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None  # commentaire de maintien
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        elif field == "id":
            self._id = value
        return None

    def _dispatch(self) -> SseEvent | None:
        raw = "\n".join(self._data)
        had_data = bool(self._data)
        name, event_id = self._event, self._id
        self._event, self._data, self._id = "message", [], None
        if not had_data:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Événement SSE ignoré (JSON invalide): %s", raw)
            return None
        return SseEvent(name, data, event_id)


class ToqueClient:
    """Appels HTTP vers le robot."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        token: str | None = None,
        timeout: float = 10,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self._token = token
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}{API_PREFIX}"

    def _headers(self, *, auth: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status == 401:
            raise ToqueAuthError("jeton refusé")
        if status == 404:
            raise ToqueNotFoundError("ressource inconnue")
        if status >= 400:
            raise ToqueConnectionError(f"HTTP {status}")

    async def _get(
        self, path: str, *, auth: bool = True, params: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with self._session.get(
                f"{self.base_url}{path}",
                headers=self._headers(auth=auth),
                params=params,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                self._raise_for_status(resp.status)
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ToqueConnectionError(str(err)) from err

    async def get_info(self) -> dict[str, Any]:
        """``/info`` : public, sans jeton."""
        return await self._get("/info", auth=False)

    async def get_state(self) -> dict[str, Any]:
        return await self._get("/state")

    async def get_session(self) -> dict[str, Any]:
        return await self._get("/session")

    async def get_recipes(self, **params: Any) -> dict[str, Any]:
        """Paramètres : q, category, favorite, limit, offset (valeurs None ignorées)."""
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._get("/recipes", params=clean)

    async def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        return await self._get(f"/recipes/{quote(recipe_id, safe='')}")

    async def stream_events(
        self, last_event_id: str | None = None
    ) -> AsyncIterator[SseEvent]:
        """Ouvre ``/events`` et produit les événements jusqu'à la fermeture du flux."""
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        parser = SseParser()
        try:
            # Maintien toutes les 15 s côté robot : 45 s de silence = liaison morte.
            async with self._session.get(
                f"{self.base_url}/events",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=45),
            ) as resp:
                self._raise_for_status(resp.status)
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    event = parser.feed(line)
                    if event is not None:
                        yield event
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ToqueConnectionError(str(err)) from err


class ToqueEventStream:
    """Boucle de lecture SSE avec reconnexion (Last-Event-ID, délai croissant)."""

    def __init__(
        self,
        client: ToqueClient,
        on_event: Callable[[SseEvent], Awaitable[None] | None],
        on_connection_change: Callable[[bool], None] | None = None,
        on_auth_error: Callable[[], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        min_delay: float = 1,
        max_delay: float = 30,
    ) -> None:
        self._client = client
        self._on_event = on_event
        self._on_connection_change = on_connection_change
        self._on_auth_error = on_auth_error
        self._sleep = sleep
        self._min_delay = min_delay
        self._max_delay = max_delay
        self.last_event_id: str | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        """Boucle sans fin ; ne se termine que sur annulation ou refus du jeton."""
        delay = self._min_delay
        while True:
            try:
                async for event in self._client.stream_events(self.last_event_id):
                    if event.id is not None:
                        self.last_event_id = event.id
                    delay = self._min_delay
                    self._notify(True)
                    res = self._on_event(event)
                    if asyncio.iscoroutine(res):
                        await res
                _LOGGER.debug("Flux SSE fermé par le robot")
            except ToqueAuthError:
                self._notify(False)
                if self._on_auth_error:
                    self._on_auth_error()
                return
            except ToqueError as err:
                _LOGGER.debug("Flux SSE interrompu: %s", err)
            self._notify(False)
            await self._sleep(delay)
            delay = min(delay * 2, self._max_delay)

    def _notify(self, connected: bool) -> None:
        if self._on_connection_change:
            self._on_connection_change(connected)
