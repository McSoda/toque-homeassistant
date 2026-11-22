"""Faux robot Toque : serveur aiohttp local imitant l'API v1."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

TOKEN = "jeton-factice-0000"

INFO: dict[str, Any] = {
    "api": 1,
    "id": "toque-test0001",
    "name": "Monsieur Cuisine",
    "model": "SKMC 1200",
    "toque": {"version": "0.5.5", "build": 55},
    "board": {"firmware": 4922},
    "android": {"release": "6.0", "sdk": 23},
    "ip": "192.0.2.10",
    "uptimeS": 86400,
    "lang": "fr",
    "recipeCount": 312,
    "recipesRev": 7,
}

STATE: dict[str, Any] = {
    "ts": 1763700000,
    "snapshotAgeMs": 120,
    "status": "recipe",
    "screenOn": True,
    "link": "READY",
    "fault": None,
    "machine": {
        "tempC": 82,
        "targetTempC": 90,
        "rpm": 0,
        "speed": 0,
        "targetSpeed": 4,
        "weightG": None,
        "scaleStable": True,
        "lidOpen": False,
        "bowlDetected": True,
        "motorOn": True,
        "heaterOn": True,
    },
    "job": {
        "kind": "MANUAL",
        "label": "Étape 3",
        "state": "RUNNING",
        "pauseReason": "",
        "phase": 1,
        "phaseCount": 1,
        "elapsedS": 40,
        "totalS": 300,
        "remainingS": 260,
        "speed": 4,
        "reverse": False,
        "accessory": "BLADE",
    },
    "sessionId": "c1d2",
}

SESSION: dict[str, Any] = {
    "active": True,
    "id": "c1d2",
    "rev": 12,
    "recipe": {"id": "veloute-de-potiron", "title": "Velouté de potiron", "category": "Soupe", "servings": 4},
    "stepIndex": 2,
    "stepCount": 8,
    "awaiting": "user",
    "step": {"index": 2, "kind": "manual", "text": "Ajouter la crème.", "machine": None,
             "weigh": {"label": "Crème", "grams": 200}},
    "lastManual": {"index": 0, "text": "Éplucher et couper le potiron."},
    "nextManual": {"index": 5, "text": "x" * 300},
    "ingredients": [{"group": "", "items": [{"qty": "800", "unit": "g", "name": "potiron", "note": ""}]}],
    "startedAt": 1763699000,
}

RECIPE: dict[str, Any] = {
    "id": "veloute-de-potiron",
    "title": "Velouté de potiron",
    "ingredients": [
        {"group": "", "items": [
            {"qty": "800", "unit": "g", "name": "potiron", "note": ""},
            {"qty": "", "unit": "", "name": "sel", "note": ""},
        ]}
    ],
}


def sse(event: str, data: Any, event_id: int | None = None) -> bytes:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class FakeToque:
    """Serveur et journal des requêtes."""

    def __init__(self) -> None:
        self.info = copy.deepcopy(INFO)
        self.state = copy.deepcopy(STATE)
        self.session = copy.deepcopy(SESSION)
        self.recipe = copy.deepcopy(RECIPE)
        self.token = TOKEN
        self.requests: list[tuple[str, dict[str, str]]] = []
        # Un élément par connexion /events : liste d'octets à envoyer ;
        # `None` en dernier = garder la connexion ouverte.
        self.event_scripts: list[list[bytes | None]] = []
        self.event_connections = 0
        self.stream_headers: list[dict[str, str]] = []
        self._server: TestServer | None = None
        self._closing = asyncio.Event()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.port

    async def start(self) -> None:
        app = web.Application()
        base = "/api/v1"
        app.router.add_get(f"{base}/info", self._info)
        app.router.add_get(f"{base}/state", self._guard(lambda r: self.state))
        app.router.add_get(f"{base}/session", self._guard(lambda r: self.session))
        app.router.add_get(f"{base}/recipes", self._guard(
            lambda r: {"rev": 7, "total": 1, "recipes": [{"id": "veloute-de-potiron", "title": "Velouté de potiron"}]}
        ))
        app.router.add_get(f"{base}/recipes/{{rid}}", self._recipe)
        app.router.add_get(f"{base}/events", self._events)
        self._server = TestServer(app, host="127.0.0.1")
        await self._server.start_server()

    async def stop(self) -> None:
        self._closing.set()  # libère les flux SSE en attente
        if self._server:
            await self._server.close()

    def _log(self, request: web.Request) -> None:
        self.requests.append((request.path_qs, dict(request.headers)))

    def _authorized(self, request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {self.token}"

    async def _info(self, request: web.Request) -> web.Response:
        self._log(request)
        return web.json_response(self.info)

    def _guard(self, producer):
        async def handler(request: web.Request) -> web.Response:
            self._log(request)
            if not self._authorized(request):
                return web.json_response({"error": "unauthorized", "message": "jeton"}, status=401)
            return web.json_response(producer(request))

        return handler

    async def _recipe(self, request: web.Request) -> web.Response:
        self._log(request)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.match_info["rid"] != self.recipe["id"]:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(self.recipe)

    async def _events(self, request: web.Request) -> web.StreamResponse:
        self._log(request)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        self.stream_headers.append(dict(request.headers))
        index = self.event_connections
        self.event_connections += 1
        script = self.event_scripts[index] if index < len(self.event_scripts) else [None]
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for chunk in script:
            if chunk is None:
                await self._closing.wait()
                break
            await resp.write(chunk)
        return resp
