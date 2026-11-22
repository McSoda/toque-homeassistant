"""Tests du config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.toque.const import CONF_TOKEN, DOMAIN

from .fake_server import TOKEN, FakeToque


async def _noop_setup(*_args, **_kwargs) -> bool:
    return True


def zeroconf(host: str, port: int) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=host,
        ip_addresses=[host],
        hostname="toque.local.",
        name="Monsieur Cuisine._toque._tcp.local.",
        port=port,
        type="_toque._tcp.local.",
        properties={"api": "1", "id": "toque-test0001", "version": "0.5.5", "name": "Monsieur Cuisine"},
    )


async def test_user_flow(hass: HomeAssistant, robot: FakeToque, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.toque.async_setup_entry", _noop_setup)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Monsieur Cuisine"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == "toque-test0001"


async def test_user_flow_errors_then_success(hass: HomeAssistant, robot: FakeToque, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.toque.async_setup_entry", _noop_setup)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: "mauvais"}
    )
    assert bad["errors"] == {"base": "invalid_auth"}

    unreachable = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1, CONF_TOKEN: TOKEN}
    )
    assert unreachable["errors"] == {"base": "cannot_connect"}

    robot.info["api"] = 2
    unsupported = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: TOKEN}
    )
    assert unsupported["errors"] == {"base": "unsupported_api"}
    robot.info["api"] = 1

    ok = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: TOKEN}
    )
    assert ok["type"] is FlowResultType.CREATE_ENTRY


async def test_zeroconf_flow(hass: HomeAssistant, robot: FakeToque, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.toque.async_setup_entry", _noop_setup)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf("127.0.0.1", robot.port),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "mauvais"})
    assert bad["errors"] == {"base": "invalid_auth"}

    ok = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    assert ok["data"][CONF_PORT] == robot.port


async def test_zeroconf_unreachable_aborts(hass: HomeAssistant, socket_enabled) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=zeroconf("127.0.0.1", 1)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_zeroconf_updates_known_robot(hass: HomeAssistant, robot: FakeToque, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.toque.async_setup_entry", _noop_setup)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="toque-test0001",
        data={CONF_HOST: "192.0.2.99", CONF_PORT: 8765, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf("127.0.0.1", robot.port),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "127.0.0.1"
    assert entry.data[CONF_PORT] == robot.port
    assert entry.data[CONF_TOKEN] == TOKEN  # le jeton n'est pas écrasé par la découverte


async def test_reauth(hass: HomeAssistant, robot: FakeToque, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.toque.async_setup_entry", _noop_setup)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="toque-test0001",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: robot.port, CONF_TOKEN: "ancien"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "toujours-faux"})
    assert bad["errors"] == {"base": "invalid_auth"}
    done = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN
