"""Tuya cloud client for Eufy robots that are not on Eufy's MQTT broker.

Ported from jeppesens/eufy-clean ``api/tuya_cloud.py`` (itself a port of the
martijnpoppen/eufy-clean TypeScript SDK), Copyright (c) Martijn Poppen, under
the Eufy-Clean License; see THIRD_PARTY_LICENSES.md. Modified from upstream:
trimmed to what this integration uses, session errors are a distinct type,
and nothing that carries the session id or the sign string is logged.

The Eufy app logs in to Tuya with the uid ``eh-<eufy user_id>``, then reads
device state and writes DPS values through signed GET requests to one
``api.json`` endpoint per region.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import random
import string
import time
import uuid
from typing import Any

import aiohttp
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    TUYA_API_ET_VERSION,
    TUYA_CERT_SIGN,
    TUYA_CLIENT_ID,
    TUYA_REGION_ORDER,
    TUYA_REGIONS,
    TUYA_SECRET,
    TUYA_SECRET2,
)

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Fields that take part in the HMAC signature
_SIGN_FIELDS = frozenset({
    "a", "v", "lat", "lon", "lang", "deviceId", "imei", "imsi",
    "appVersion", "ttid", "isH5", "h5Token", "os", "clientId",
    "postData", "time", "requestId", "n4h5", "sid", "sp", "et",
})
# AES key/iv of the loginEx password derivation (fixed in the Tuya SDK)
_AES_KEY = bytes([36, 78, 109, 138, 86, 172, 135, 145, 36, 67, 45, 139, 108, 188, 162, 196])
_AES_IV = bytes([119, 36, 86, 242, 167, 102, 76, 243, 57, 44, 53, 151, 233, 62, 87, 71])
_SESSION_ERRORS = ("USER_SESSION_INVALID", "USER_SESSION_LOSS", "NO_SID")


class TuyaError(Exception):
    """Tuya cloud request failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"Tuya {code}: {message}")
        self.code = code
        self.message = message


class TuyaSessionError(TuyaError):
    """The Tuya session is missing or expired; log in again."""


def _md5(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()


def _mobile_hash(data: str) -> str:
    """MD5 with Tuya's digit shuffle, used for postData in the signature."""
    h = _md5(data)
    return h[8:16] + h[0:8] + h[24:32] + h[16:24]


def _is_hex(s: str) -> bool:
    return bool(set(s.lower()) & set("abcdef"))


def _encrypt_password(uid: str, public_key_n: str, exponent: int) -> str:
    """loginEx password: AES-128-CBC the uid, MD5 the hex, raw-RSA that MD5."""
    padded_len = 16 * math.ceil(len(uid) / 16)
    padded_uid = uid.rjust(padded_len, "0")
    encryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV), backend=default_backend()).encryptor()
    encrypted = encryptor.update(padded_uid.encode()) + encryptor.finalize()
    password_md5 = _md5(encrypted.hex().upper())
    is_hex = _is_hex(public_key_n)
    n = int(public_key_n, 16) if is_hex else int(public_key_n)
    c = pow(int.from_bytes(password_md5.encode(), "big"), exponent, n)
    key_size = len(public_key_n) // 2 if is_hex else (n.bit_length() + 7) // 8
    return c.to_bytes(max(key_size, (c.bit_length() + 7) // 8), "big").hex()


class TuyaCloud:
    """One Tuya cloud session for one region."""

    def __init__(self, session: aiohttp.ClientSession, region: str) -> None:
        self.region = region
        self.endpoint = TUYA_REGIONS.get(region, TUYA_REGIONS["EU"])
        self.sid: str | None = None
        self._session = session
        self._device_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=44))
        self._hmac_key = f"{TUYA_CERT_SIGN}_{TUYA_SECRET2}_{TUYA_SECRET}"

    async def login(self, eufy_user_id: str) -> None:
        """Tuya loginEx with the Eufy user id; follows a region redirect if told to."""
        uid = f"eh-{eufy_user_id}"
        token = await self.request(
            "tuya.m.user.uid.token.create",
            {"countryCode": self.region, "uid": uid},
            requires_sid=False,
        )
        try:
            passwd = _encrypt_password(uid, token["publicKey"], int(token["exponent"]))
            login_token = token["token"]
        except (KeyError, TypeError, ValueError) as err:
            raise TuyaError("BAD_RESPONSE", "token.create returned no usable key") from err
        result = await self.request(
            "tuya.m.user.uid.password.login",
            {
                "countryCode": self.region,
                "uid": uid,
                "createGroup": True,
                "passwd": passwd,
                "ifencrypt": 1,
                "options": {"group": 1},
                "token": login_token,
            },
            requires_sid=False,
        )
        if not isinstance(result, dict) or not result.get("sid"):
            raise TuyaError("BAD_RESPONSE", "password.login returned no session")
        domain = result.get("domain") or {}
        mobile_api_url = domain.get("mobileApiUrl")
        if isinstance(mobile_api_url, str) and mobile_api_url and not self.endpoint.startswith(mobile_api_url):
            self.endpoint = mobile_api_url.rstrip("/") + "/api.json"
            # Keep ``region`` one of our own keys (it is persisted and used to pick
            # the endpoint next time); Tuya's regionCode values are not those keys.
            self.region = next((k for k, v in TUYA_REGIONS.items() if v == self.endpoint), self.region)
            _LOGGER.debug("Tuya redirected the session to %s", self.region)
        self.sid = result["sid"]
        _LOGGER.debug("Tuya login succeeded in region %s", self.region)

    async def request(
        self,
        action: str,
        data: dict[str, Any] | None = None,
        *,
        version: str = "1.0",
        requires_sid: bool = True,
        gid: str | None = None,
    ) -> Any:
        """One signed Tuya API call; returns the ``result`` field."""
        if requires_sid and not self.sid:
            raise TuyaSessionError("NO_SID", "not logged in")
        params: dict[str, Any] = {
            "a": action,
            "deviceId": self._device_id,
            "sdkVersion": "3.0.0cAnker",
            "os": "Android",
            "lang": "en",
            "appVersion": "3.8.5",
            "v": version,
            "clientId": TUYA_CLIENT_ID,
            "time": int(time.time()),
            "et": TUYA_API_ET_VERSION,
            "ttid": "android",
            "appRnVersion": "5.11",
            "platform": "Android",
            "requestId": str(uuid.uuid4()),
        }
        if data is not None:
            params["postData"] = json.dumps(data, separators=(",", ":"))
        if gid is not None:
            params["gid"] = gid
        if requires_sid:
            params["sid"] = self.sid
        params["sign"] = self._sign(params)
        try:
            async with self._session.get(self.endpoint, params=params, timeout=_TIMEOUT) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TuyaError("NETWORK", f"{action}: {err}") from err
        if not isinstance(body, dict):
            raise TuyaError("BAD_RESPONSE", action)
        if body.get("success") is False:
            code = str(body.get("errorCode", "UNKNOWN"))
            message = str(body.get("errorMsg", "unknown error"))
            if code in _SESSION_ERRORS:
                raise TuyaSessionError(code, message)
            raise TuyaError(code, message)
        return body.get("result")

    async def get_devices(self) -> list[dict[str, Any]]:
        """Every device the account can see: first location group plus shared ones."""
        groups = await self.request("tuya.m.location.list") or []
        devices: list[dict[str, Any]] = []
        for group in groups:
            gid = group.get("groupId")
            if not gid:
                continue
            devices.extend(await self.request("tuya.m.my.group.device.list", gid=gid) or [])
            break  # the Eufy app only reads the first group
        devices.extend(await self.request("tuya.m.my.shared.device.list") or [])
        return devices

    async def get_dps(self, device_id: str) -> dict[str, Any] | None:
        """Last-known DPS snapshot of one device, or None if it isn't listed."""
        for device in await self.get_devices():
            if device.get("devId") == device_id:
                return device.get("dps") or {}
        return None

    async def send(self, device_id: str, dps: dict[str, Any]) -> None:
        """Write DPS values to a device."""
        _LOGGER.debug("Tuya -> %s: %s", device_id, dps)
        await self.request(
            "tuya.m.device.dp.publish", {"dps": dps, "devId": device_id, "gwId": device_id}
        )

    async def get_rtc_config(self, device_id: str) -> dict[str, Any] | None:
        """P2P credentials for one device.

        Every call reserves a session slot on the robot (about ten hours) that
        only opening a P2P session with exactly this ``session`` releases, so
        call it right before opening one and never cache the result.
        """
        result = await self.request("m.ipc.v4.rtc.config.get", {"devId": device_id})
        if not result:
            return None
        p2p = result.get("p2pConfig") or {}
        return {
            "device_password": result.get("password", ""),
            "ice_config": {
                "token": p2p.get("ices", []),
                "tcp_token": p2p.get("tcpRelay", {}),
                "log": p2p.get("log", {}),
            },
            "session": p2p.get("session") or {},
        }

    def _sign(self, params: dict[str, Any]) -> str:
        parts = []
        for key in sorted(params):
            if key not in _SIGN_FIELDS:
                continue
            value = params.get(key)
            if value is None or value == "":
                continue
            parts.append(f"{key}={_mobile_hash(str(value)) if key == 'postData' else value}")
        return hmac.new(self._hmac_key.encode(), "||".join(parts).encode(), hashlib.sha256).hexdigest()


async def async_tuya_login(
    session: aiohttp.ClientSession,
    eufy_user_id: str,
    regions: tuple[str, ...] = TUYA_REGION_ORDER,
) -> TuyaCloud:
    """Log in to the first region that accepts the account; raise the last error."""
    error: TuyaError | None = None
    for region in regions:
        client = TuyaCloud(session, region)
        try:
            await client.login(eufy_user_id)
        except TuyaError as err:
            _LOGGER.debug("Tuya login in region %s failed: %s", region, err)
            error = err
            continue
        return client
    assert error is not None
    raise error
