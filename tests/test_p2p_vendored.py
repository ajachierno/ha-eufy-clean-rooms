"""Smoke tests for the vendored Tuya P2P client (framing, KCP records, stream chunks)."""

from __future__ import annotations

import hashlib
import struct

from custom_components.eufy_clean_rooms.p2p import control, framing, kcp, streams


def test_frame_round_trip() -> None:
    key = b"0123456789abcdef"
    data = framing.encode_frame(key, framing.CMD_RTC, {"header": {"type": "offer"}}, 7)
    frames, rest = framing.decode_frames(key, data)
    assert rest == b"" and frames == [(framing.CMD_RTC, {"header": {"type": "offer"}})]


def test_kcp_record_round_trip() -> None:
    key = b"k" * 16
    assert kcp.decrypt_record(key, kcp.encrypt_record(key, b"hello")) == b"hello"


def _chunk(name: str, index: int, payload: bytes, total: int, last: bool) -> bytes:
    header = bytearray(streams.HEADER_LENGTH)
    header[0:8] = streams.SYNC
    struct.pack_into("<H", header, 8, 1)
    struct.pack_into("<I", header, 12, index)
    header[20 : 20 + len(name)] = name.encode()
    struct.pack_into("<III", header, 68, len(payload), total, int(last))
    return bytes(header) + payload


def test_stream_reassembler_joins_chunks() -> None:
    reassembler = streams.StreamReassembler()
    first = _chunk(streams.MAP_STREAM, 0, b"abc", 6, False)
    second = _chunk(streams.MAP_STREAM, 1, b"def", 6, True)
    assert list(reassembler.feed(first)) == []
    assert list(reassembler.feed(second[:40])) == []  # split mid-header
    assert list(reassembler.feed(second[40:])) == [(streams.MAP_STREAM, b"abcdef")]


def test_auth_credential() -> None:
    assert control.auth_credential("pw", "key") == hashlib.md5(b"pw||key").hexdigest()


def test_start_packets_begin_with_auth() -> None:
    packets = control.start_packets("pw", "key")
    assert len(packets) == 3
    assert packets[0][:4] == (0x12345678).to_bytes(4, "little")


def test_no_video_symbols() -> None:
    assert not hasattr(control, "video_start_packets")
    assert not hasattr(control, "video_prelude_packets")
    assert not hasattr(kcp, "CONV_VIDEO")
