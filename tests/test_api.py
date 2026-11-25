"""Tests du client API : parsing, SSE, reconnexion."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from custom_components.toque.api import (
    SseParser,
    ToqueAuthError,
    ToqueBusyError,
    ToqueClient,
    ToqueConnectionError,
    ToqueEventStream,
    ToqueNotFoundError,
)

from .fake_server import TOKEN, FakeToque, sse


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def make_client(session, robot: FakeToque, token: str | None = TOKEN) -> ToqueClient:
    return ToqueClient(session, "127.0.0.1", robot.port, token)


def test_sse_parser_basic() -> None:
    parser = SseParser()
    lines = [": keepalive", "id: 4", "event: state", 'data: {"a": 1}', ""]
    events = [e for line in lines if (e := parser.feed(line))]
    assert len(events) == 1
    assert (events[0].event, events[0].id, events[0].data) == ("state", "4", {"a": 1})


def test_sse_parser_multiline_and_defaults() -> None:
    parser = SseParser()
    out = [parser.feed(line) for line in ["data: {", 'data: "x": 2', "data: }", ""]]
    assert out[-1].event == "message" and out[-1].id is None and out[-1].data == {"x": 2}


def test_sse_parser_ignores_invalid_json_and_empty_blocks() -> None:
    parser = SseParser()
    assert parser.feed("") is None
    parser.feed("data: pas du json")
    assert parser.feed("") is None
    # Les champs ne fuient pas d'un bloc à l'autre.
    parser.feed("id: 9")
    parser.feed("event: step")
    parser.feed("data: {}")
    assert parser.feed("").id == "9"
    parser.feed("data: {}")
    nxt = parser.feed("")
    assert nxt.id is None and nxt.event == "message"


async def test_info_is_public(session, robot: FakeToque) -> None:
    info = await make_client(session, robot, token=None).get_info()
    assert info["id"] == "toque-test0001"
    _, headers = robot.requests[-1]
    assert "Authorization" not in headers


async def test_state_and_session_parsing(session, robot: FakeToque) -> None:
    client = make_client(session, robot)
    state = await client.get_state()
    assert state["machine"]["weightG"] is None
    assert state["job"]["remainingS"] == 260
    assert (await client.get_session())["stepCount"] == 8
    assert robot.requests[-1][1]["Authorization"] == f"Bearer {TOKEN}"


async def test_errors(session, robot: FakeToque) -> None:
    with pytest.raises(ToqueAuthError):
        await make_client(session, robot, token="mauvais").get_state()
    with pytest.raises(ToqueNotFoundError):
        await make_client(session, robot).get_recipe("inconnue")
    recipe = await make_client(session, robot).get_recipe("veloute-de-potiron")
    assert recipe["title"] == "Velouté de potiron"
    await robot.stop()
    with pytest.raises(ToqueConnectionError):
        await make_client(session, robot).get_state()


async def test_recipes_params_drop_none(session, robot: FakeToque) -> None:
    await make_client(session, robot).get_recipes(q="soupe", favorite=None, limit=5)
    path, _ = robot.requests[-1]
    assert "q=" in path and "limit=5" in path and "favorite" not in path


async def test_stream_events(session, robot: FakeToque) -> None:
    robot.event_scripts = [
        [b": hello\n\n", sse("state", {"status": "standby"}), sse("done", {"result": "finished"}, 7)]
    ]
    events = [e async for e in make_client(session, robot).stream_events()]
    assert [(e.event, e.id) for e in events] == [("state", None), ("done", "7")]


async def test_stream_auth_error(session, robot: FakeToque) -> None:
    with pytest.raises(ToqueAuthError):
        async for _ in make_client(session, robot, token="x").stream_events():
            pass


async def test_reconnect_sends_last_event_id(session, robot: FakeToque) -> None:
    robot.event_scripts = [
        [sse("state", {"status": "standby"}), sse("step", {"index": 1}, 1), sse("step", {"index": 2}, 2)],
        [sse("step", {"index": 3}, 3), None],
    ]
    received: list[tuple[str, str | None]] = []
    sleeps: list[float] = []
    connected: list[bool] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def on_event(event) -> None:
        received.append((event.event, event.id))

    stream = ToqueEventStream(
        make_client(session, robot),
        on_event,
        on_connection_change=connected.append,
        sleep=fake_sleep,
    )
    stream.start()
    for _ in range(100):
        if len(received) >= 4:
            break
        await asyncio.sleep(0.05)
    await stream.stop()

    assert received == [("state", None), ("step", "1"), ("step", "2"), ("step", "3")]
    assert "Last-Event-ID" not in robot.stream_headers[0]
    assert robot.stream_headers[1]["Last-Event-ID"] == "2"
    assert sleeps == [1]  # un délai avant la reconnexion
    assert False in connected and connected[-1] is True


async def test_backoff_grows_then_resets(session, robot: FakeToque) -> None:
    await robot.stop()  # connexions refusées
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 6:
            raise asyncio.CancelledError

    stream = ToqueEventStream(make_client(session, robot), lambda e: None, sleep=fake_sleep, max_delay=8)
    with pytest.raises(asyncio.CancelledError):
        await stream.run()
    assert sleeps == [1, 2, 4, 8, 8, 8]


async def test_auth_error_stops_stream_and_notifies(session, robot: FakeToque) -> None:
    flagged: list[bool] = []
    stream = ToqueEventStream(
        make_client(session, robot, token="mauvais"),
        lambda e: None,
        on_auth_error=lambda: flagged.append(True),
    )
    await asyncio.wait_for(stream.run(), 5)
    assert flagged == [True]


async def test_connection_error_messages_are_never_empty(session, robot: FakeToque) -> None:
    # Un délai dépassé a un str() vide : le message doit quand même dire quoi et où.
    class Slow:
        def get(self, *args, **kwargs):
            raise asyncio.TimeoutError

    client = ToqueClient(Slow(), "192.0.2.10", 8765, TOKEN)
    with pytest.raises(ToqueConnectionError) as excinfo:
        await client.get_state()
    message = str(excinfo.value)
    assert "TimeoutError" in message and "192.0.2.10:8765/api/v1/state" in message

    await robot.stop()
    with pytest.raises(ToqueConnectionError) as excinfo:
        await make_client(session, robot).get_state()
    assert f"127.0.0.1:{robot.port}/api/v1/state" in str(excinfo.value)
    assert not str(excinfo.value).startswith(":")


async def test_http_error_message_contains_url(session, robot: FakeToque) -> None:
    client = make_client(session, robot)
    with pytest.raises(ToqueConnectionError) as excinfo:
        client._raise_for_status(503, "http://x/api/v1/state")
    assert "HTTP 503" in str(excinfo.value) and "http://x/api/v1/state" in str(excinfo.value)


async def test_silent_stream_times_out(session, robot: FakeToque) -> None:
    robot.event_scripts = [[sse("state", {"status": "recipe"}), None]]
    client = ToqueClient(session, "127.0.0.1", robot.port, TOKEN, stream_read_timeout=0.4)
    received = []
    with pytest.raises(ToqueConnectionError) as excinfo:
        async for event in client.stream_events():
            received.append(event.event)
    assert received == ["state"]
    assert "SocketTimeoutError" in str(excinfo.value)


async def test_pings_keep_stream_alive(session, robot: FakeToque) -> None:
    # Un commentaire `: ping` toutes les 0,2 s (délai de lecture : 0,5 s) ne doit pas couper le flux.
    ping = b": ping\n\n"
    robot.event_scripts = [[sse("state", {"status": "standby"}), 0.2, ping, 0.2, ping, 0.2, ping, 0.2, ping,
                            sse("done", {"result": "finished"}, 1)]]
    client = ToqueClient(session, "127.0.0.1", robot.port, TOKEN, stream_read_timeout=0.5)
    events = [e.event async for e in client.stream_events()]
    assert events == ["state", "done"]


async def test_silent_stream_forgets_last_event_id(session, robot: FakeToque) -> None:
    # Après un redémarrage du robot, ses identifiants repartent de 1 : un Last-Event-ID plus grand
    # lui fait ignorer tous les événements. Un flux muet doit donc faire oublier l'identifiant.
    robot.event_scripts = [
        [sse("step", {"index": 1}, 41), None],  # puis silence : délai de lecture dépassé
        [sse("state", {"status": "standby"}), None],
    ]
    client = ToqueClient(session, "127.0.0.1", robot.port, TOKEN, stream_read_timeout=0.4)
    received: list[str] = []

    async def on_event(event) -> None:
        received.append(event.event)

    async def no_sleep(_delay: float) -> None:
        return None

    stream = ToqueEventStream(client, on_event, sleep=no_sleep)
    stream.start()
    for _ in range(100):
        if len(received) >= 2:
            break
        await asyncio.sleep(0.05)
    await stream.stop()

    assert received == ["step", "state"]
    assert robot.stream_headers[0].get("Last-Event-ID") is None
    assert "Last-Event-ID" not in robot.stream_headers[1]  # identifiant 41 oublié
    assert stream.last_event_id is None


async def test_lower_event_id_replaces_stale_one(session, robot: FakeToque) -> None:
    robot.event_scripts = [[sse("step", {"index": 1}, 41), sse("step", {"index": 2}, 3), None]]
    seen: list[str | None] = []

    async def on_event(event) -> None:
        seen.append(event.id)

    stream = ToqueEventStream(make_client(session, robot), on_event)
    stream.start()
    for _ in range(100):
        if len(seen) >= 2:
            break
        await asyncio.sleep(0.05)
    await stream.stop()
    assert seen == ["41", "3"]
    assert stream.last_event_id == "3"  # le compteur du robot a redémarré


async def test_recipe_image_and_etag(session, robot: FakeToque) -> None:
    client = make_client(session, robot)
    image = await client.get_recipe_image("veloute-de-potiron")
    assert (image.content, image.content_type, image.etag) == (
        robot.image, "image/jpeg", robot.image_etag,
    )
    assert "If-None-Match" not in robot.requests[-1][1]
    # inchangée : 304
    assert await client.get_recipe_image("veloute-de-potiron", image.etag) is None
    assert robot.requests[-1][1]["If-None-Match"] == robot.image_etag
    assert robot.image_statuses == [200, 304]


async def test_recipe_image_errors(session, robot: FakeToque) -> None:
    client = make_client(session, robot)
    robot.image_busy = 1
    with pytest.raises(ToqueBusyError) as busy:
        await client.get_recipe_image("veloute-de-potiron")
    assert busy.value.retry_after == 5
    robot.image_busy, robot.retry_after = 1, None
    with pytest.raises(ToqueBusyError) as busy:
        await client.get_recipe_image("veloute-de-potiron")
    assert busy.value.retry_after is None
    robot.image = None
    with pytest.raises(ToqueNotFoundError):
        await client.get_recipe_image("veloute-de-potiron")
    with pytest.raises(ToqueAuthError):
        await make_client(session, robot, "mauvais").get_recipe_image("veloute-de-potiron")
