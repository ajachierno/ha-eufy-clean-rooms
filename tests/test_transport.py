"""Transport layer: MQTT delegation and Tuya cloud write/poll with one re-login retry."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.eufy_clean_rooms.api import EufyApiError
from custom_components.eufy_clean_rooms.transport import MqttTransport, TuyaTransport
from custom_components.eufy_clean_rooms.tuya import TuyaSessionError


def tuya_transport(send_effects=None, dps_effects=None):
    tuya = MagicMock()
    tuya.send = AsyncMock(side_effect=send_effects)
    tuya.get_dps = AsyncMock(side_effect=dps_effects)
    relogin = AsyncMock()
    return TuyaTransport(tuya, "dev1", relogin), tuya, relogin


async def test_tuya_send_relogins_once_on_session_error() -> None:
    transport, tuya, relogin = tuya_transport(send_effects=[TuyaSessionError("USER_SESSION_INVALID", "x"), None])
    await transport.start()
    await transport.send({"152": "AggG"})
    assert relogin.await_count == 1 and tuya.send.await_count == 2
    assert tuya.send.await_args.args == ("dev1", {"152": "AggG"})


async def test_tuya_poll_second_failure_raises_api_error() -> None:
    err = TuyaSessionError("USER_SESSION_LOSS", "gone")
    transport, tuya, relogin = tuya_transport(dps_effects=[err, err])
    with pytest.raises(EufyApiError, match="USER_SESSION_LOSS"):
        await transport.poll()
    assert relogin.await_count == 1


async def test_tuya_poll_returns_dps() -> None:
    transport, tuya, relogin = tuya_transport(dps_effects=[{"163": 87}])
    assert await transport.poll() == {"163": 87}
    assert relogin.await_count == 0


async def test_tuya_poll_interval_and_connected() -> None:
    transport, _, _ = tuya_transport()
    assert transport.poll_interval == timedelta(seconds=30)
    assert transport.connected is False
    await transport.start()
    assert transport.connected is True
    await transport.stop()
    assert transport.connected is False


async def test_mqtt_transport_delegates() -> None:
    mqtt = MagicMock()
    mqtt.send = AsyncMock()
    mqtt.connect = AsyncMock()
    mqtt.disconnect = AsyncMock()
    mqtt.connected = True
    cloud = MagicMock()
    cloud.get_device = AsyncMock(return_value={"device_sn": "sn", "dps": {"163": 50}})
    transport = MqttTransport(mqtt, cloud, "sn")
    await transport.start()
    await transport.send({"160": True})
    mqtt.connect.assert_awaited_once()
    mqtt.send.assert_awaited_once_with({"160": True})
    assert await transport.poll() == {"163": 50}
    assert transport.connected is True and transport.poll_interval == timedelta(minutes=10)
    await transport.stop()
    mqtt.disconnect.assert_awaited_once()


async def test_mqtt_poll_relogins_once() -> None:
    mqtt = MagicMock()
    cloud = MagicMock()
    cloud.get_device = AsyncMock(side_effect=[EufyApiError("expired"), {"dps": {"163": 1}}])
    cloud.login = AsyncMock()
    transport = MqttTransport(mqtt, cloud, "sn")
    assert await transport.poll() == {"163": 1}
    cloud.login.assert_awaited_once()
