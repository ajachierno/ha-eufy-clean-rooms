"""Eufy cloud client: HTTP login/device discovery plus the AWS IoT MQTT channel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import json
import logging
import os
import ssl
import tempfile
import time
from typing import Any

import aiohttp
from paho.mqtt import client as mqtt

from .const import (
    LOGIN_CONFIGS,
    MQTT_PORT,
    URL_AIOT_DEVICES,
    URL_DEVICE_V2,
    URL_MQTT_INFO,
    URL_USER_INFO,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class EufyAuthError(Exception):
    """Login rejected (bad email/password)."""


class EufyApiError(Exception):
    """Cloud request failed."""


class EufyCloud:
    """Talks to the Eufy account REST APIs."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str, openudid: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self.openudid = openudid
        self._access_token: str | None = None
        self._user_center_token: str | None = None
        self._gtoken: str | None = None

    async def login(self) -> None:
        """Log in, trying the unified eufy app first, then the Eufy Clean app."""
        last: Any = None
        for url, client_id, secret, category in LOGIN_CONFIGS:
            headers = {
                "category": category,
                "openudid": self.openudid,
                "clientType": "1",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "language": "en",
                "country": "US",
            }
            body = {
                "email": self._email,
                "password": self._password,
                "client_id": client_id,
                "client_secret": secret,
            }
            try:
                async with self._session.post(url, json=body, headers=headers, timeout=_TIMEOUT) as resp:
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                last = err
                continue
            if data and data.get("access_token"):
                self._access_token = data["access_token"]
                await self._load_user_center()
                return
            last = data
        if isinstance(last, dict) and last.get("res_code") in (26006, 26050, 26052, 100033):
            raise EufyAuthError(last.get("message") or "invalid credentials")
        raise EufyAuthError(f"login failed: {last}")

    async def _load_user_center(self) -> None:
        headers = {
            "token": self._access_token,
            "openudid": self.openudid,
            "user-agent": USER_AGENT,
            "category": "Home",
            "clienttype": "2",
        }
        data = await self._get(URL_USER_INFO, headers)
        center_id = data.get("user_center_id")
        if not center_id:
            raise EufyApiError("no user_center_id in user info")
        self._user_center_token = data.get("user_center_token")
        self._gtoken = hashlib.md5(center_id.encode()).hexdigest()

    def _aiot_headers(self) -> dict[str, str]:
        return {
            "user-agent": USER_AGENT,
            "openudid": self.openudid,
            "os-version": "Android",
            "model-type": "PHONE",
            "app-name": "eufy_home",
            "x-auth-token": self._user_center_token or "",
            "gtoken": self._gtoken or "",
            "content-type": "application/json; charset=UTF-8",
            "language": "en",
            "country": "US",
        }

    async def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise EufyApiError(f"GET {url}: {err}") from err
        return data.get("data", data) if isinstance(data, dict) else {}

    async def _post(self, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._session.post(
                url, json=body or {}, headers=self._aiot_headers(), timeout=_TIMEOUT
            ) as resp:
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise EufyApiError(f"POST {url}: {err}") from err
        if not isinstance(data, dict):
            raise EufyApiError(f"POST {url}: unexpected response")
        if data.get("code") not in (None, 0) and data.get("data") is None:
            raise EufyApiError(f"POST {url}: {data.get('code')} {data.get('msg')}")
        return data.get("data", data) or {}

    async def get_mqtt_devices(self) -> list[dict[str, Any]]:
        """Devices reachable over the Eufy MQTT channel (the ones we can drive)."""
        data = await self._post(URL_AIOT_DEVICES, {"attribute": 3})
        return [d.get("device", d) for d in data.get("devices") or []]

    async def get_account_devices(self) -> list[dict[str, Any]]:
        """Every device on the account (used for names and diagnostics)."""
        headers = {
            "token": self._access_token or "",
            "openudid": self.openudid,
            "user-agent": USER_AGENT,
            "category": "Home",
            "clienttype": "2",
        }
        try:
            data = await self._get(URL_DEVICE_V2, headers)
        except EufyApiError:
            return []
        return data.get("devices") or []

    async def get_device(self, device_sn: str) -> dict[str, Any] | None:
        for dev in await self.get_mqtt_devices():
            if dev.get("device_sn") == device_sn:
                return dev
        return None

    async def get_mqtt_credentials(self) -> dict[str, Any]:
        data = await self._post(URL_MQTT_INFO)
        if not data.get("certificate_pem"):
            raise EufyApiError("no MQTT certificate returned")
        return data


def _build_tls_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    """Blocking: load the per-user client certificate into an SSL context."""
    ctx = ssl.create_default_context()
    cert_fd, cert_path = tempfile.mkstemp(suffix=".pem")
    key_fd, key_path = tempfile.mkstemp(suffix=".key")
    try:
        with os.fdopen(cert_fd, "w") as f:
            f.write(cert_pem)
        with os.fdopen(key_fd, "w") as f:
            f.write(key_pem)
        ctx.load_cert_chain(cert_path, key_path)
    finally:
        for path in (cert_path, key_path):
            try:
                os.unlink(path)
            except OSError:
                pass
    return ctx


class EufyMqtt:
    """MQTT link to one robot via Eufy's AWS IoT broker."""

    def __init__(
        self,
        creds: dict[str, Any],
        openudid: str,
        device_sn: str,
        device_model: str,
        on_dps: Callable[[dict[str, Any]], None],
        on_connection: Callable[[bool], None],
    ) -> None:
        self._creds = creds
        self._sn = device_sn
        self._model = device_model
        self._on_dps = on_dps
        self._on_connection = on_connection
        self._loop: asyncio.AbstractEventLoop | None = None
        self.user_id: str = creds["user_id"]
        base = f"android-{creds['app_name']}-eufy_android_{openudid}_{self.user_id}"
        self._client_id = f"{base}-{int(time.time() * 1000)}"
        self._client: mqtt.Client | None = None
        self._seq = 0

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected())

    @property
    def _res_topic(self) -> str:
        return f"cmd/eufy_home/{self._model}/{self._sn}/res"

    @property
    def _req_topic(self) -> str:
        return f"cmd/eufy_home/{self._model}/{self._sn}/req"

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        ctx = await self._loop.run_in_executor(
            None, _build_tls_context, self._creds["certificate_pem"], self._creds["private_key"]
        )
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
        )
        client.username_pw_set(self._creds["thing_name"])
        client.tls_set_context(ctx)
        client.on_connect = self._handle_connect
        client.on_disconnect = self._handle_disconnect
        client.on_message = self._handle_message
        client.reconnect_delay_set(min_delay=5, max_delay=300)
        self._client = client
        await self._loop.run_in_executor(
            None, client.connect, self._creds["endpoint_addr"], MQTT_PORT, 60
        )
        client.loop_start()

    async def disconnect(self) -> None:
        if not self._client:
            return
        client, self._client = self._client, None
        client.loop_stop()
        await asyncio.get_running_loop().run_in_executor(None, client.disconnect)

    def _handle_connect(
        self, client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any = None
    ) -> None:
        if reason.is_failure:
            _LOGGER.error("Eufy MQTT connect refused: %s", reason)
            return
        client.subscribe(self._res_topic)
        _LOGGER.debug("Eufy MQTT connected, subscribed to %s", self._res_topic)
        if self._loop:
            self._loop.call_soon_threadsafe(self._on_connection, True)

    def _handle_disconnect(
        self, _client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any = None
    ) -> None:
        if reason.is_failure:
            _LOGGER.warning("Eufy MQTT disconnected unexpectedly (%s), paho will retry", reason)
        if self._loop:
            self._loop.call_soon_threadsafe(self._on_connection, False)

    def _handle_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            outer = json.loads(msg.payload.decode("utf-8", errors="replace"))
            payload = outer.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            dps = payload.get("data")
        except (ValueError, AttributeError):
            _LOGGER.debug("Unparseable MQTT message on %s", msg.topic)
            return
        if isinstance(dps, dict) and dps and self._loop:
            self._loop.call_soon_threadsafe(self._on_dps, dps)

    async def send(self, dps: dict[str, Any]) -> None:
        """Publish a DPS write, wrapped the way the Android app wraps it."""
        if not self.connected:
            raise EufyApiError("MQTT not connected")
        now = int(time.time() * 1000)
        self._seq += 1
        inner = json.dumps(
            {"account_id": self.user_id, "data": dps, "device_sn": self._sn, "protocol": 2, "t": now}
        )
        message = {
            "head": {
                "client_id": self._client_id,
                "cmd": 65537,
                "cmd_status": 2,
                "msg_seq": self._seq,
                "seed": "",
                "sess_id": self._client_id,
                "sign_code": 0,
                "timestamp": now,
                "version": "1.0.0.1",
            },
            "payload": inner,
        }
        _LOGGER.debug("Eufy MQTT -> %s: %s", self._req_topic, dps)
        assert self._client is not None
        self._client.publish(self._req_topic, json.dumps(message))
