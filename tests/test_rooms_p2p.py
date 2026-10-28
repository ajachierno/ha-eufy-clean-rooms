"""Room discovery over the P2P map channel, with the session faked."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.eufy_clean_rooms import proto, rooms_p2p
from custom_components.eufy_clean_rooms.p2p import P2PError, P2PRefused, framing
from custom_components.eufy_clean_rooms.p2p.streams import MAP_STREAM
from custom_components.eufy_clean_rooms.rooms_p2p import (
    DockBusy,
    DockUnreachable,
    RoomsUnavailable,
    fetch_rooms,
    probe_dock,
)

RTC = {"device_password": "p", "ice_config": {}, "session": {"sessionId": "s"}}


def map_object(map_info: bytes) -> bytes:
    return base64.b64decode(proto.to_b64(proto.f_varint(1, 0) + proto.f_bytes(2, map_info)))


ROOMS_OBJ = map_object(
    proto.f_varint(2, 1) + proto.f_varint(8, 4) + proto.f_bytes(
        12,
        proto.f_bytes(2, proto.f_varint(1, 1) + proto.f_bytes(2, b"Laundry room"))
        + proto.f_bytes(2, proto.f_varint(1, 0))
        + proto.f_varint(3, 1),
    )
)
OUTLINE_OBJ = map_object(proto.f_varint(2, 1) + proto.f_varint(8, 1))


class FakeSession:
    """Stands in for P2PSession: emits the given objects, then waits to be closed."""

    objects: list[tuple[str, bytes]] = []
    failure: Exception | None = None
    instances: list[FakeSession] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = asyncio.Event()
        FakeSession.instances.append(self)

    async def run(self) -> None:
        if self.failure:
            raise self.failure
        for name, obj in self.objects:
            self.kwargs["on_object"](name, obj)
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


@pytest.fixture
def fake_session():
    FakeSession.objects, FakeSession.failure, FakeSession.instances = [], None, []
    with patch("custom_components.eufy_clean_rooms.rooms_p2p.P2PSession", FakeSession):
        yield FakeSession


async def _fetch(timeout: float = 2.0) -> proto.RoomTable:
    return await fetch_rooms(host="192.0.2.5", local_key="k", device_id="dev1", rtc=RTC, timeout=timeout)


async def test_fetch_rooms_returns_table(fake_session) -> None:
    fake_session.objects = [(MAP_STREAM, ROOMS_OBJ)]
    table = await _fetch()
    assert table.map_id == 1
    assert [(r.id, r.name) for r in table.rooms] == [(1, "Laundry room"), (0, "Room 0")]
    session = fake_session.instances[0]
    assert session.kwargs["cloud_session"] == RTC["session"] and session.kwargs["device_password"] == "p"
    assert session.closed.is_set()


async def test_fetch_rooms_ignores_outline_then_takes_rooms(fake_session) -> None:
    fake_session.objects = [(MAP_STREAM, OUTLINE_OBJ), ("cleanPath.bin.stream", b"xx"), (MAP_STREAM, ROOMS_OBJ)]
    assert (await _fetch()).map_id == 1


async def test_fetch_rooms_timeout(fake_session) -> None:
    fake_session.objects = [(MAP_STREAM, OUTLINE_OBJ)]
    with pytest.raises(RoomsUnavailable):
        await _fetch(timeout=0.1)
    assert fake_session.instances[0].closed.is_set()


async def test_fetch_rooms_refused_is_busy(fake_session) -> None:
    fake_session.failure = P2PRefused("robot sent disconnect")
    with pytest.raises(DockBusy):
        await _fetch()


async def test_fetch_rooms_other_error_is_unavailable(fake_session) -> None:
    fake_session.failure = P2PError("no map stream from the robot")
    with pytest.raises(RoomsUnavailable, match="no map stream"):
        await _fetch()


async def test_probe_dock_classifies() -> None:
    async def refused(*args, **kwargs):
        raise ConnectionRefusedError

    async def slow(*args, **kwargs):
        raise TimeoutError

    with patch("custom_components.eufy_clean_rooms.rooms_p2p.asyncio.open_connection", refused):
        with pytest.raises(DockBusy):
            await probe_dock("192.0.2.5")
    with patch("custom_components.eufy_clean_rooms.rooms_p2p.asyncio.open_connection", slow):
        with pytest.raises(DockUnreachable):
            await probe_dock("192.0.2.5", timeout=0.1)


def test_decrypt_discovery_datagram() -> None:
    key = hashlib.md5(b"yGAdlopoPVldABfn").digest()
    plain = json.dumps({"gwId": "dev1", "ip": "192.0.2.5", "version": "3.3"}).encode()
    pad = 16 - len(plain) % 16
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    body = enc.update(plain + bytes([pad]) * pad) + enc.finalize()
    datagram = b"\x00" * 20 + body + b"\x00" * 8
    assert rooms_p2p._parse_discovery(datagram) == {"gwId": "dev1", "ip": "192.0.2.5", "version": "3.3"}
    assert rooms_p2p._parse_discovery(b"garbage") is None


DOCK_KEY = "0123456789abcdef"


async def _fake_tuya_device(key: str):
    """A localhost server that answers any frame like a Tuya 3.3 device keyed with `key`."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        reply = framing.encode_frame(key.encode(), framing.CMD_DP_QUERY, {"dps": {"1": True}}, 1)
        # Devices put a 4-byte return code ahead of the ciphertext.
        body = b"\x00\x00\x00\x00" + reply[16:-8]
        head = reply[:12] + (len(body) + 8).to_bytes(4, "big")
        writer.write(head + body + reply[-8:])
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_is_dock_matches_only_its_own_key() -> None:
    server, port = await _fake_tuya_device(DOCK_KEY)
    async with server:
        with patch.object(rooms_p2p, "DOCK_PORT", port):
            assert await rooms_p2p.is_dock("127.0.0.1", "dev", DOCK_KEY)
            assert not await rooms_p2p.is_dock("127.0.0.1", "dev", "fedcba9876543210")


async def test_is_dock_closed_port() -> None:
    with patch.object(rooms_p2p, "DOCK_PORT", 9):
        assert not await rooms_p2p.is_dock("127.0.0.1", "dev", DOCK_KEY, timeout=0.5)


async def test_scan_for_dock_returns_the_match() -> None:
    async def fake_is_dock(host, device_id, local_key):
        return host == "192.0.2.7"

    with patch.object(rooms_p2p, "is_dock", fake_is_dock):
        hosts = [f"192.0.2.{i}" for i in range(1, 20)]
        assert await rooms_p2p.scan_for_dock(hosts, "dev", DOCK_KEY) == "192.0.2.7"
        assert await rooms_p2p.scan_for_dock(["192.0.2.1"], "dev", DOCK_KEY) is None
        assert await rooms_p2p.scan_for_dock(hosts, "dev", "") is None
