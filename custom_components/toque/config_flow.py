"""Config flow : découverte mDNS ou saisie manuelle, puis jeton."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import ToqueAuthError, ToqueClient, ToqueError
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN, SUPPORTED_API

_LOGGER = logging.getLogger(__name__)


class ToqueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configuration d'un robot Toque."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._info: dict[str, Any] = {}

    async def _validate(self, host: str, port: int, token: str | None) -> dict[str, Any]:
        """/info (public) valide l'adresse ; /state valide le jeton si fourni."""
        client = ToqueClient(async_get_clientsession(self.hass), host, port, token)
        info = await client.get_info()
        if not isinstance(info, dict) or "id" not in info:
            raise ToqueError("réponse /info inattendue")
        if info.get("api") != SUPPORTED_API:
            raise UnsupportedApi
        if token is not None:
            await client.get_state()
        return info

    # --- saisie manuelle ---------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            try:
                info = await self._validate(host, port, user_input[CONF_TOKEN])
            except ToqueAuthError:
                errors["base"] = "invalid_auth"
            except UnsupportedApi:
                errors["base"] = "unsupported_api"
            except ToqueError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: user_input[CONF_TOKEN]}
                )
                return self._create(info, host, port, user_input[CONF_TOKEN])

        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                    vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )

    # --- découverte mDNS -----------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        try:
            info = await self._validate(host, port, None)
        except UnsupportedApi:
            return self.async_abort(reason="unsupported_api")
        except ToqueError:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(info["id"])
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
        self._host, self._port, self._info = host, port, info
        self.context["title_placeholders"] = {"name": info.get("name") or "Monsieur Cuisine"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._host is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._validate(self._host, self._port, user_input[CONF_TOKEN])
            except ToqueAuthError:
                errors["base"] = "invalid_auth"
            except ToqueError:
                errors["base"] = "cannot_connect"
            else:
                return self._create(self._info, self._host, self._port, user_input[CONF_TOKEN])
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            description_placeholders={
                "name": self._info.get("name") or "Monsieur Cuisine",
                "host": self._host,
            },
            errors=errors,
        )

    # --- jeton refusé ----------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._validate(entry.data[CONF_HOST], entry.data[CONF_PORT], user_input[CONF_TOKEN])
            except ToqueAuthError:
                errors["base"] = "invalid_auth"
            except ToqueError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    def _create(self, info: dict[str, Any], host: str, port: int, token: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=info.get("name") or "Monsieur Cuisine",
            data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
        )


class UnsupportedApi(ToqueError):
    """Version d'API non gérée par cette intégration."""
