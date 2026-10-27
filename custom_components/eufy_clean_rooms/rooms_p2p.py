"""Room list over the robot's local P2P map channel.

Tuya-cloud robots (S1 Pro and friends) put their room table on no DPS. The
Eufy app reads it over an encrypted P2P session on the LAN; this module opens
one such session, takes the first ROOM_PARAMS object and closes again.

The caller decides when to fetch and must call :func:`probe_dock` before
asking the Tuya cloud for the RTC config: every config fetch reserves a session
slot on the robot that only opening that session releases.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import proto
from .p2p import P2PError, P2PRefused, P2PSession
from .p2p.streams import MAP_STREAM

_LOGGER = logging.getLogger(__name__)

DOCK_PORT = 6668
DISCOVERY_PORT = 6667
# Tuya's well-known key for the encrypted 3.x discovery broadcasts
_DISCOVERY_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()


class DockBusy(Exception):
    """The dock refused the connection: its few local slots are taken (app, local integrations)."""


class DockUnreachable(Exception):
    """No route to the dock from here."""


class RoomsUnavailable(Exception):
    """The P2P session opened but no room table arrived."""


async def probe_dock(host: str, timeout: float = 3.0) -> None:
    """Raise DockBusy or DockUnreachable unless the dock accepts a TCP connection."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, DOCK_PORT), timeout)
    except (ConnectionRefusedError, ConnectionResetError) as err:
        raise DockBusy(f"{host} refused the connection") from err
    except (TimeoutError, OSError) as err:
        raise DockUnreachable(f"{host}: {err or 'timed out'}") from err
    writer.close()


def _parse_discovery(datagram: bytes) -> dict[str, Any] | None:
    """Decrypt one Tuya discovery broadcast; None when it isn't one."""
    body = datagram[20:-8]
    if len(body) < 16 or len(body) % 16:
        return None
    try:
        dec = Cipher(algorithms.AES(_DISCOVERY_KEY), modes.ECB()).decryptor()
        plain = dec.update(body) + dec.finalize()
        plain = plain[: -plain[-1]]
        data = json.loads(plain)
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


def _listen_for_dock(device_id: str, timeout: float) -> str | None:
    """Blocking: wait for the dock's discovery broadcast and return its address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(1.0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                datagram, (address, _) = sock.recvfrom(4096)
            except TimeoutError:
                continue
            data = _parse_discovery(datagram)
            if data and data.get("gwId") == device_id:
                return address
    except OSError as err:
        _LOGGER.debug("Dock discovery not possible: %s", err)
    finally:
        sock.close()
    return None


async def find_dock_host(device_id: str, timeout: float = 6.0) -> str | None:
    """Find the dock's LAN address from Tuya's discovery broadcasts (UDP 6667)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _listen_for_dock, device_id, timeout)


async def fetch_rooms(
    *,
    host: str,
    local_key: str,
    device_id: str,
    rtc: dict[str, Any],
    timeout: float = 20.0,
) -> proto.RoomTable:
    """Open one P2P session and return the room table it sends."""
    loop = asyncio.get_running_loop()
    table_future: asyncio.Future[proto.RoomTable] = loop.create_future()

    def on_object(name: str, obj: bytes) -> None:
        if name != MAP_STREAM or table_future.done():
            return
        table = proto.parse_p2p_room_table(obj)
        if table is not None and table.rooms:
            table_future.set_result(table)

    session = P2PSession(
        host=host,
        local_key=local_key,
        device_id=device_id,
        device_password=rtc["device_password"],
        ice_config=rtc.get("ice_config"),
        cloud_session=rtc.get("session") or {},
        on_object=on_object,
    )
    runner = asyncio.ensure_future(session.run())
    try:
        done, _ = await asyncio.wait({table_future, runner}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if table_future in done:
            return table_future.result()
        if runner in done:
            runner.result()  # raises the session's error
            raise RoomsUnavailable("the session ended before sending the room list")
        raise RoomsUnavailable(f"no room list from {host} within {timeout:.0f}s")
    except P2PRefused as err:
        raise DockBusy(str(err)) from err
    except P2PError as err:
        raise RoomsUnavailable(str(err)) from err
    finally:
        table_future.cancel()
        await session.close()
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, P2PError):
            pass
