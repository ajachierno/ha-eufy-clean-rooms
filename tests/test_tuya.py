"""Tuya cloud client against mocked Tuya endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.eufy_clean_rooms.const import (
    TUYA_CLIENT_ID,
    TUYA_REGIONS,
    TUYA_SECRET,
    TUYA_SECRET2,
)
from custom_components.eufy_clean_rooms.tuya import (
    TuyaCloud,
    TuyaError,
    TuyaSessionError,
    _mobile_hash,
    async_tuya_login,
)

EU, US = TUYA_REGIONS["EU"], TUYA_REGIONS["US"]
TOKEN = {"publicKey": "c3", "exponent": "3", "token": "tok"}  # tiny hex modulus is fine for raw RSA
LOGIN_OK = {"sid": "s1", "domain": {}}


def tuya_mock(aioclient_mock, endpoint: str, answers: dict[str, Any], calls: list[tuple[str, dict]] | None = None):
    """Answer each Tuya action on one endpoint; an answer that is an Exception becomes success=false."""

    async def respond(method, url, data):  # noqa: ARG001
        query = parse_qs(urlparse(str(url)).query)
        action = query["a"][0]
        if calls is not None:
            calls.append((action, query))
        answer = answers.get(action)
        if isinstance(answer, dict) and answer.get("success") is False:
            body = answer
        else:
            body = {"success": True, "result": answer}
        return AiohttpClientMockResponse(method, url, text=json.dumps(body))

    aioclient_mock.get(endpoint, side_effect=respond)


def test_sign_matches_known_vector() -> None:
    client = TuyaCloud(MagicMock(), "US")
    client._device_id = "d" * 44
    params = {
        "a": "x", "deviceId": client._device_id, "time": 1, "clientId": TUYA_CLIENT_ID,
        "postData": '{"k":1}', "et": "0.0.1", "v": "1.0", "sign": "ignored", "os": "Android",
    }
    expected_msg = (
        f"a=x||clientId={TUYA_CLIENT_ID}||deviceId={'d' * 44}||et=0.0.1||"
        f"os=Android||postData={_mobile_hash('{\"k\":1}')}||time=1||v=1.0"
    )
    expected = hmac.new(f"A_{TUYA_SECRET2}_{TUYA_SECRET}".encode(), expected_msg.encode(), hashlib.sha256).hexdigest()
    assert client._sign(params) == expected


async def test_login_falls_back_to_us(hass: HomeAssistant, aioclient_mock) -> None:
    calls: list = []
    tuya_mock(aioclient_mock, EU, {
        "tuya.m.user.uid.token.create": TOKEN,
        "tuya.m.user.uid.password.login": {"success": False, "errorCode": "USER_NOT_EXISTS", "errorMsg": "no"},
    }, calls)
    tuya_mock(aioclient_mock, US, {"tuya.m.user.uid.token.create": TOKEN, "tuya.m.user.uid.password.login": LOGIN_OK}, calls)
    client = await async_tuya_login(async_get_clientsession(hass), "uid-1")
    assert client.region == "US" and client.sid == "s1"
    assert [a for a, _ in calls] == [
        "tuya.m.user.uid.token.create", "tuya.m.user.uid.password.login",
        "tuya.m.user.uid.token.create", "tuya.m.user.uid.password.login",
    ]


async def test_login_eu_first_no_us_call(hass: HomeAssistant, aioclient_mock) -> None:
    tuya_mock(aioclient_mock, EU, {"tuya.m.user.uid.token.create": TOKEN, "tuya.m.user.uid.password.login": LOGIN_OK})
    client = await async_tuya_login(async_get_clientsession(hass), "uid-1")
    assert client.region == "EU"
    assert all(US not in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_login_all_regions_fail_raises(hass: HomeAssistant, aioclient_mock) -> None:
    bad = {"success": False, "errorCode": "USER_NOT_EXISTS", "errorMsg": "no"}
    for ep in (EU, US):
        tuya_mock(aioclient_mock, ep, {"tuya.m.user.uid.token.create": TOKEN, "tuya.m.user.uid.password.login": bad})
    with pytest.raises(TuyaError, match="USER_NOT_EXISTS"):
        await async_tuya_login(async_get_clientsession(hass), "uid-1")


async def test_login_redirect_updates_endpoint(hass: HomeAssistant, aioclient_mock) -> None:
    tuya_mock(aioclient_mock, EU, {
        "tuya.m.user.uid.token.create": TOKEN,
        "tuya.m.user.uid.password.login": {"sid": "s1", "domain": {"mobileApiUrl": "https://a1.tuyaus.com", "regionCode": "US"}},
    })
    client = await async_tuya_login(async_get_clientsession(hass), "uid-1")
    assert client.endpoint == "https://a1.tuyaus.com/api.json" and client.region == "US"


async def _logged_in(hass, aioclient_mock, answers: dict, calls: list | None = None) -> TuyaCloud:
    tuya_mock(aioclient_mock, US, {
        "tuya.m.user.uid.token.create": TOKEN, "tuya.m.user.uid.password.login": LOGIN_OK, **answers,
    }, calls)
    return await async_tuya_login(async_get_clientsession(hass), "uid-1", regions=("US",))


async def test_get_devices_and_dps(hass: HomeAssistant, aioclient_mock) -> None:
    client = await _logged_in(hass, aioclient_mock, {
        "tuya.m.location.list": [{"groupId": "g1"}, {"groupId": "g2"}],
        "tuya.m.my.group.device.list": [{"devId": "dev1", "dps": {"163": 87}, "localKey": "k"}],
        "tuya.m.my.shared.device.list": [{"devId": "dev2", "dps": {}}],
    })
    devices = await client.get_devices()
    assert [d["devId"] for d in devices] == ["dev1", "dev2"]
    assert await client.get_dps("dev1") == {"163": 87}
    assert await client.get_dps("nope") is None


async def test_send_publishes_dps(hass: HomeAssistant, aioclient_mock) -> None:
    calls: list = []
    client = await _logged_in(hass, aioclient_mock, {"tuya.m.device.dp.publish": True}, calls)
    await client.send("dev1", {"152": "AggG"})
    action, query = calls[-1]
    assert action == "tuya.m.device.dp.publish"
    assert json.loads(query["postData"][0]) == {"dps": {"152": "AggG"}, "devId": "dev1", "gwId": "dev1"}
    assert query["sid"] == ["s1"] and "sign" in query


async def test_session_error_type(hass: HomeAssistant, aioclient_mock) -> None:
    client = await _logged_in(hass, aioclient_mock, {
        "tuya.m.location.list": {"success": False, "errorCode": "USER_SESSION_INVALID", "errorMsg": "expired"},
    })
    with pytest.raises(TuyaSessionError):
        await client.get_devices()


async def test_request_without_sid_is_session_error() -> None:
    client = TuyaCloud(MagicMock(), "US")
    with pytest.raises(TuyaSessionError):
        await client.request("tuya.m.location.list")


async def test_rtc_config_shape(hass: HomeAssistant, aioclient_mock) -> None:
    client = await _logged_in(hass, aioclient_mock, {
        "m.ipc.v4.rtc.config.get": {
            "password": "p", "p2pConfig": {"ices": [1], "tcpRelay": {"t": 1}, "log": {"l": 1}, "session": {"sessionId": "x"}},
        },
    })
    assert await client.get_rtc_config("dev1") == {
        "device_password": "p",
        "ice_config": {"token": [1], "tcp_token": {"t": 1}, "log": {"l": 1}},
        "session": {"sessionId": "x"},
    }


async def test_login_bad_result_is_tuya_error(hass: HomeAssistant, aioclient_mock) -> None:
    tuya_mock(aioclient_mock, US, {"tuya.m.user.uid.token.create": TOKEN, "tuya.m.user.uid.password.login": None})
    with pytest.raises(TuyaError, match="BAD_RESPONSE"):
        await async_tuya_login(async_get_clientsession(hass), "uid-1", regions=("US",))


async def test_login_redirect_keeps_a_known_region_key(hass: HomeAssistant, aioclient_mock) -> None:
    tuya_mock(aioclient_mock, EU, {
        "tuya.m.user.uid.token.create": TOKEN,
        "tuya.m.user.uid.password.login": {"sid": "s1", "domain": {"mobileApiUrl": "https://a1.tuyaus.com", "regionCode": "AZ"}},
    })
    client = await async_tuya_login(async_get_clientsession(hass), "uid-1")
    assert client.region == "US" and client.endpoint == TUYA_REGIONS["US"]
