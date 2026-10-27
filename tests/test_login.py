"""Login flow against mocked Eufy HTTP endpoints."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.eufy_clean_rooms.api import EufyApiError, EufyAuthError, EufyCloud
from custom_components.eufy_clean_rooms.const import DOMAIN, LOGIN_CONFIGS, URL_USER_INFO

V2_URL, V1_URL = LOGIN_CONFIGS[0][0], LOGIN_CONFIGS[1][0]
BAD_PASSWORD = {"res_code": 26006, "message": "Incorrect password"}


async def test_falls_back_when_first_login_has_no_user_center(hass: HomeAssistant, aioclient_mock) -> None:
    """v2 accepts the password but has no user center; v1 does. Use v1."""
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    aioclient_mock.post(V2_URL, json={"access_token": "tok-v2"})
    aioclient_mock.post(V1_URL, json={"access_token": "tok-v1"})
    answers = [
        '{"res_code": 1, "message": "no user center", "data": null}',
        '{"user_center_id": "uc1", "user_center_token": "uct"}',
    ]

    async def user_info(method, url, data):  # noqa: ARG001
        return AiohttpClientMockResponse(method, url, text=answers.pop(0))

    aioclient_mock.get(URL_USER_INFO, side_effect=user_info)
    cloud = EufyCloud(async_get_clientsession(hass), "a@b.c", "pw", "udid")
    await cloud.login()
    assert answers == []
    assert cloud._access_token == "tok-v1" and cloud._user_center_token == "uct"
    assert [str(c[1]) for c in aioclient_mock.mock_calls if c[0] == "POST"] == [V2_URL, V1_URL]


async def test_wrong_password_is_auth_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(V2_URL, json=BAD_PASSWORD)
    aioclient_mock.post(V1_URL, json=BAD_PASSWORD)
    cloud = EufyCloud(async_get_clientsession(hass), "a@b.c", "nope", "udid")
    with pytest.raises(EufyAuthError, match="Incorrect password"):
        await cloud.login()


async def test_token_without_any_user_center_still_logs_in(hass: HomeAssistant, aioclient_mock) -> None:
    """Tuya-only accounts never get a user center; the login must still yield the user id."""
    aioclient_mock.post(V2_URL, json={"access_token": "t", "user_id": "uid-1"})
    aioclient_mock.post(V1_URL, json={"access_token": "t", "user_id": "uid-1"})
    aioclient_mock.get(URL_USER_INFO, json={"data": None})
    cloud = EufyCloud(async_get_clientsession(hass), "a@b.c", "pw", "udid")
    await cloud.login()
    assert cloud.has_user_center is False and cloud.user_id == "uid-1"
    with pytest.raises(EufyApiError, match="user center"):
        await cloud.get_mqtt_devices()
    with pytest.raises(EufyApiError, match="user center"):
        await cloud.get_mqtt_credentials()
    assert await cloud.get_device("x") is None
    assert all(c[0] == "POST" or str(c[1]) == URL_USER_INFO for c in aioclient_mock.mock_calls)


async def test_user_id_captured_with_user_center(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(V2_URL, json={"access_token": "t", "user_id": "uid-1"})
    aioclient_mock.get(URL_USER_INFO, json={"user_center_id": "uc1", "user_center_token": "uct"})
    cloud = EufyCloud(async_get_clientsession(hass), "a@b.c", "pw", "udid")
    await cloud.login()
    assert cloud.has_user_center is True and cloud.user_id == "uid-1"


async def test_form_shows_eufy_reason(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(V2_URL, json=BAD_PASSWORD)
    aioclient_mock.post(V1_URL, json=BAD_PASSWORD)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: " a@b.c ", CONF_PASSWORD: "nope"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert "Incorrect password" in result["description_placeholders"]["details"]
    assert "nope" not in result["description_placeholders"]["details"]
