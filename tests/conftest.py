"""Fixtures communes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import sys

import pytest

from .fake_server import FakeToque

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Active le chargement des intégrations personnalisées."""
    return


@pytest.fixture
async def robot() -> AsyncIterator[FakeToque]:
    server = FakeToque()
    await server.start()
    yield server
    await server.stop()


if sys.platform == "win32":
    # Sous Windows, asyncio crée une paire de sockets TCP locale pour sa boucle :
    # le blocage des sockets du plugin Home Assistant (prévu pour Linux) la refuserait.
    # Réservé au poste de développement ; la CI (Linux) garde le blocage.
    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None
