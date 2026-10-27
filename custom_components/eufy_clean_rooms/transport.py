"""How the coordinator reaches a robot: Eufy MQTT (push) or the Tuya cloud (polling).

Both transports speak the same DPS dictionaries; the coordinator does not care
which one it holds beyond the poll interval and the ``connected`` flag.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from typing import Any, Protocol

from .api import EufyApiError, EufyCloud, EufyMqtt
from .const import TUYA_POLL_SECONDS
from .tuya import TuyaCloud, TuyaError, TuyaSessionError

_LOGGER = logging.getLogger(__name__)


class Transport(Protocol):
    """What the coordinator needs from a link to the robot."""

    poll_interval: timedelta

    @property
    def connected(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, dps: dict[str, Any]) -> None: ...

    async def poll(self) -> dict[str, Any] | None: ...


class MqttTransport:
    """Eufy's AWS IoT MQTT broker: push updates, with a slow cloud backfill poll."""

    poll_interval = timedelta(minutes=10)

    def __init__(self, mqtt: EufyMqtt, cloud: EufyCloud, device_sn: str) -> None:
        self._mqtt = mqtt
        self._cloud = cloud
        self._device_sn = device_sn

    @property
    def connected(self) -> bool:
        return self._mqtt.connected

    async def start(self) -> None:
        await self._mqtt.connect()

    async def stop(self) -> None:
        await self._mqtt.disconnect()

    async def send(self, dps: dict[str, Any]) -> None:
        await self._mqtt.send(dps)

    async def poll(self) -> dict[str, Any] | None:
        """The cloud's last-known DPS snapshot; one re-login if the session lapsed."""
        try:
            device = await self._cloud.get_device(self._device_sn)
        except EufyApiError:
            await self._cloud.login()
            device = await self._cloud.get_device(self._device_sn)
        return device.get("dps") if device else None


class TuyaTransport:
    """The Tuya cloud: DPS writes and a 30 s poll of the device list."""

    poll_interval = timedelta(seconds=TUYA_POLL_SECONDS)

    def __init__(
        self, tuya: TuyaCloud, device_id: str, relogin: Callable[[], Awaitable[None]]
    ) -> None:
        self._tuya = tuya
        self._device_id = device_id
        self._relogin = relogin
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        self._connected = True

    async def stop(self) -> None:
        self._connected = False

    async def _with_relogin(self, call: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await call()
        except TuyaSessionError as err:
            _LOGGER.debug("Tuya session lapsed (%s); logging in again", err.code)
            try:
                await self._relogin()
                return await call()
            except TuyaError as retry_err:
                raise EufyApiError(str(retry_err)) from retry_err
        except TuyaError as err:
            raise EufyApiError(str(err)) from err

    async def send(self, dps: dict[str, Any]) -> None:
        await self._with_relogin(lambda: self._tuya.send(self._device_id, dps))

    async def poll(self) -> dict[str, Any] | None:
        return await self._with_relogin(lambda: self._tuya.get_dps(self._device_id))
