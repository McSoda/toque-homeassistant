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

from .const import API_PREFIX, STREAM_READ_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)


class ToqueError(Exception):
    """Erreur générique de l'API Toque."""


class ToqueConnectionError(ToqueError):
    """Le robot ne répond pas (réseau, délai, 503, réponse illisible)."""


class ToqueBusyError(ToqueConnectionError):
    """Le robot refuse pour l'instant (503) ; réessayer après ``retry_after`` secondes."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ToqueStreamTimeout(ToqueConnectionError):
    """Le flux SSE est resté muet (aucun octet, pings compris) au-delà du délai de lecture."""


class ToqueAuthError(ToqueError):
    """Jeton refusé (401)."""


class ToqueNotFoundError(ToqueError):
    """Ressource inconnue (404)."""


def describe_error(err: BaseException, url: str) -> str:
    """Message lisible : `str()` d'un délai dépassé ou d'une coupure est souvent vide."""
    detail = str(err).strip()
    name = type(err).__name__
    return f"{name}: {detail} ({url})" if detail else f"{name} ({url})"


def _retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds > 0 else None


@dataclass(frozen=True)
class RecipeImage:
    """Image d'une recette telle que le robot l'a en cache."""

    content: bytes
    content_type: str
    etag: str | None = None


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
        stream_read_timeout: float = STREAM_READ_TIMEOUT_S,
    ) -> None:
        self._session = session
        self._stream_read_timeout = stream_read_timeout
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
    def _raise_for_status(status: int, url: str) -> None:
        if status == 401:
            raise ToqueAuthError("jeton refusé")
        if status == 404:
            raise ToqueNotFoundError("ressource inconnue")
        if status >= 400:
            raise ToqueConnectionError(f"HTTP {status} ({url})")

    async def _get(
        self, path: str, *, auth: bool = True, params: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(
                url,
                headers=self._headers(auth=auth),
                params=params,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                self._raise_for_status(resp.status, url)
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ToqueConnectionError(describe_error(err, url)) from err

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

    async def get_recipe_image(
        self, recipe_id: str, etag: str | None = None
    ) -> RecipeImage | None:
        """``/recipes/{id}/image`` ; ``None`` si l'image n'a pas changé depuis ``etag`` (304).

        Le robot répond 404 si l'image n'est pas en cache et 503 pendant un programme.
        """
        url = f"{self.base_url}/recipes/{quote(recipe_id, safe='')}/image"
        headers = self._headers()
        headers["Accept"] = "image/*"
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with self._session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=self._timeout)
            ) as resp:
                if resp.status == 304 and etag:
                    return None
                if resp.status == 503:
                    raise ToqueBusyError(
                        f"HTTP 503 ({url})", _retry_after(resp.headers.get("Retry-After"))
                    )
                self._raise_for_status(resp.status, url)
                content = await resp.read()
                if resp.status != 200 or not resp.content_type.startswith("image/") or not content:
                    raise ToqueConnectionError(f"réponse sans image ({url})")
                return RecipeImage(content, resp.content_type, resp.headers.get("ETag"))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ToqueConnectionError(describe_error(err, url)) from err

    async def stream_events(
        self, last_event_id: str | None = None
    ) -> AsyncIterator[SseEvent]:
        """Ouvre ``/events`` et produit les événements jusqu'à la fermeture du flux."""
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        parser = SseParser()
        url = f"{self.base_url}/events"
        try:
            # Maintien toutes les 15 s côté robot : un silence plus long = liaison morte.
            async with self._session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=self._stream_read_timeout),
            ) as resp:
                self._raise_for_status(resp.status, url)
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    event = parser.feed(line)
                    if event is not None:
                        yield event
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            kind = ToqueStreamTimeout if isinstance(err, asyncio.TimeoutError) else ToqueConnectionError
            raise kind(describe_error(err, url)) from err


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
            except ToqueStreamTimeout as err:
                # Flux muet : le robot a peut-être redémarré (ses identifiants repartent de 1
                # et il ignore un Last-Event-ID plus grand). On repart sans identifiant.
                _LOGGER.debug("Flux SSE muet, identifiant oublié (%s): %s", self.last_event_id, err)
                self.last_event_id = None
            except ToqueError as err:
                _LOGGER.debug("Flux SSE interrompu: %s", err)
            self._notify(False)
            await self._sleep(delay)
            delay = min(delay * 2, self._max_delay)

    def _notify(self, connected: bool) -> None:
        if self._on_connection_change:
            self._on_connection_change(connected)
