"""Minimal protobuf wire-format codec for the Eufy Clean messages we use.

Eufy's "novel" robovacs put length-delimited protobuf messages, base64-encoded,
into Tuya-style DPS slots. Rather than ship generated *_pb2 modules (and fight
over HA's bundled protobuf version), this module encodes/decodes the few
messages needed by hand. Field numbers come from Eufy's .proto definitions as
published in martijnpoppen/eufy-clean (see NOTICE.md).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- wire format


def _varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7


def f_varint(num: int, value: int) -> bytes:
    """Encode a varint field. Zero is still emitted (harmless, and explicit)."""
    return _varint(num << 3) + _varint(int(value))


def f_bytes(num: int, payload: bytes) -> bytes:
    """Encode a length-delimited field (sub-message, string or bytes)."""
    return _varint((num << 3) | 2) + _varint(len(payload)) + payload


def parse(buf: bytes) -> dict[int, list[Any]]:
    """Parse one message level into {field_number: [values...]}.

    Varints come back as int, length-delimited fields as bytes (callers decide
    whether that's a string or a nested message). Fixed32/64 come back as bytes.
    """
    out: dict[int, list[Any]] = {}
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _read_varint(buf, pos)
        elif wire == 2:
            ln, pos = _read_varint(buf, pos)
            val = buf[pos : pos + ln]
            pos += ln
        elif wire == 1:
            val, pos = buf[pos : pos + 8], pos + 8
        elif wire == 5:
            val, pos = buf[pos : pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.setdefault(num, []).append(val)
    return out


def _first(msg: dict[int, list[Any]], num: int, default: Any = None) -> Any:
    vals = msg.get(num)
    return vals[0] if vals else default


def _sub(msg: dict[int, list[Any]], num: int) -> dict[int, list[Any]] | None:
    raw = _first(msg, num)
    return parse(raw) if isinstance(raw, bytes) else None


def to_b64(message: bytes) -> str:
    """Length-prefix (protobuf 'delimited') and base64, as the robot expects."""
    return base64.b64encode(_varint(len(message)) + message).decode()


def from_b64(value: str) -> bytes:
    """Undo to_b64: base64-decode and strip the varint length prefix."""
    raw = base64.b64decode(value)
    if not raw:
        return b""
    _, pos = _read_varint(raw, 0)
    return raw[pos:]


# ---------------------------------------------------------------- enums

# ModeCtrlRequest.Method (control.proto)
START_AUTO_CLEAN = 0
START_SELECT_ROOMS_CLEAN = 1
START_SPOT_CLEAN = 3
START_GOHOME = 6
STOP_TASK = 12
PAUSE_TASK = 13
RESUME_TASK = 14

# CleanType.Value (clean_param.proto)
CLEAN_TYPES = {"vacuum": 0, "mop": 1, "vacuum_and_mop": 2, "vacuum_then_mop": 3}
# Fan.Suction
SUCTIONS = {"Quiet": 0, "Standard": 1, "Turbo": 2, "Max": 3, "Max+": 4}
# MopMode.Level
MOP_LEVELS = {"low": 0, "medium": 1, "high": 2}

# WorkStatus.State (work_status.proto)
WS_STANDBY, WS_SLEEP, WS_FAULT, WS_CHARGING = 0, 1, 2, 3
WS_POSITIONING, WS_CLEANING, WS_REMOTE, WS_GO_HOME, WS_CRUISING = 4, 5, 6, 7, 8

# RoomScene.Type (common.proto) -> default names the app shows for unnamed rooms
SCENE_NAMES = {
    1: "Study",
    2: "Bedroom",
    3: "Bathroom",
    4: "Kitchen",
    5: "Living Room",
    6: "Dining Room",
    7: "Corridor",
}

# ---------------------------------------------------------------- builders


def mode_ctrl(method: int) -> str:
    """ModeCtrlRequest with just a method (start/pause/resume/stop/go home)."""
    # START_AUTO_CLEAN is an empty message ("AA=="), byte-identical to what the
    # Eufy app sends an S1 Pro.
    body = b"" if method == START_AUTO_CLEAN else f_varint(1, method)
    if method == START_SPOT_CLEAN:
        body += f_bytes(6, f_varint(1, 1))  # SpotClean{clean_times=1}
    return to_b64(body)


def select_rooms_clean(room_ids: list[int], map_id: int, clean_times: int = 1) -> str:
    """ModeCtrlRequest{method=START_SELECT_ROOMS_CLEAN, select_rooms_clean{...}}."""
    inner = b"".join(
        f_bytes(1, f_varint(1, rid) + f_varint(2, order))
        for order, rid in enumerate(room_ids, start=1)
    )
    inner += f_varint(2, max(1, clean_times)) + f_varint(3, map_id)
    # mode=GENERAL(0): clean params come from CleanParamRequest.area_clean_param
    return to_b64(f_varint(1, START_SELECT_ROOMS_CLEAN) + f_bytes(4, inner))


def clean_param_request(
    clean_type: str | None = None,
    suction: str | None = None,
    mop_level: str | None = None,
    clean_times: int | None = None,
) -> str:
    """CleanParamRequest setting both the auto and the room/area parameters."""
    param = b""
    if clean_type is not None:
        param += f_bytes(1, f_varint(1, CLEAN_TYPES[clean_type]))
    if mop_level is not None:
        param += f_bytes(4, f_varint(1, MOP_LEVELS[mop_level]))
    if suction is not None:
        param += f_bytes(6, f_varint(1, SUCTIONS[suction]))
    if clean_times:
        param += f_varint(7, clean_times)
    return to_b64(f_bytes(1, param) + f_bytes(2, param))


# ---------------------------------------------------------------- parsers


@dataclass
class Room:
    id: int
    name: str


@dataclass
class RoomTable:
    map_id: int
    rooms: list[Room] = field(default_factory=list)


def _room_name(room_id: int, name: str, scene: dict[int, list[Any]] | None) -> str:
    name = name.strip()
    if name:
        return name
    if scene:
        base = SCENE_NAMES.get(_first(scene, 1, 0))
        index_msg = _sub(scene, 2)
        index = _first(index_msg, 1, 0) if index_msg else 0
        if base:
            return f"{base} {index}" if index and index > 1 else base
    return f"Room {room_id}"


def _dedupe(rooms: list[Room]) -> list[Room]:
    seen: dict[str, int] = {}
    for room in rooms:
        key = room.name.casefold()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            room.name = f"{room.name} ({seen[key]})"
    return rooms


def parse_room_table(value: str) -> RoomTable | None:
    """Parse DPS 165 into rooms.

    Newer firmware sends UniversalDataResponse{cur_map_room{map_id, data[]}},
    older firmware sends RoomParams{rooms[] = 2, map_id = 3}. Try both.
    """
    try:
        msg = parse(from_b64(value))
    except (ValueError, TypeError):
        return None

    # UniversalDataResponse: field 1 = RoomTable{map_id=1, repeated Data=2{id=1,name=2,scene=3}}
    table = _sub(msg, 1)
    if table and (1 in table or 2 in table):
        try:
            rooms = []
            for raw in table.get(2, []):
                r = parse(raw)
                rid = _first(r, 1, 0)
                rooms.append(Room(rid, _room_name(rid, _first(r, 2, b"").decode(), _sub(r, 3))))
            if rooms or _first(table, 1):
                return RoomTable(_first(table, 1, 0), _dedupe(rooms))
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass

    # RoomParams: repeated Room rooms=2{id=1,name=2,scene=4}, map_id=3
    if 2 in msg or 3 in msg:
        try:
            rooms = []
            for raw in msg.get(2, []):
                r = parse(raw)
                rid = _first(r, 1, 0)
                rooms.append(Room(rid, _room_name(rid, _first(r, 2, b"").decode(), _sub(r, 4))))
            return RoomTable(_first(msg, 3, 0), _dedupe(rooms))
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass
    return None


@dataclass
class WorkStatus:
    state: int
    mode: int  # WorkStatus.Mode.Value: 0 auto, 1 select room, 3 spot ...
    paused: bool
    go_wash_mode: int | None
    charging_state: int | None


def parse_work_status(value: str) -> WorkStatus | None:
    """Parse DPS 153 WorkStatus."""
    try:
        msg = parse(from_b64(value))
    except (ValueError, TypeError):
        return None
    mode_msg = _sub(msg, 1)
    cleaning = _sub(msg, 6)
    go_wash = _sub(msg, 7)
    go_home = _sub(msg, 8)
    charging = _sub(msg, 3)
    paused = bool(
        (cleaning and _first(cleaning, 1, 0) == 1) or (go_home and _first(go_home, 1, 0) == 1)
    )
    return WorkStatus(
        state=_first(msg, 2, 0),
        mode=_first(mode_msg, 1, 0) if mode_msg else 0,
        paused=paused,
        go_wash_mode=_first(go_wash, 2, 0) if go_wash is not None else None,
        charging_state=_first(charging, 1, 0) if charging is not None else None,
    )


def parse_error_code(value: Any) -> list[int]:
    """Parse DPS 177 ErrorCode -> active error codes (falls back to warnings)."""
    if isinstance(value, int):
        return [value] if value > 0 else []
    try:
        msg = parse(from_b64(value))
    except (ValueError, TypeError):
        return []

    def _codes(num: int) -> list[int]:
        codes: list[int] = []
        for v in msg.get(num, []):
            if isinstance(v, int):
                codes.append(v)
            else:  # packed repeated uint32
                pos = 0
                while pos < len(v):
                    c, pos = _read_varint(v, pos)
                    codes.append(c)
        return codes

    return _codes(2) or _codes(3)


def parse_clean_type(value: str) -> str | None:
    """Pull the configured clean type out of a DPS 154 CleanParamResponse."""
    try:
        msg = parse(from_b64(value))
    except (ValueError, TypeError):
        return None
    param = _sub(msg, 1)
    if not param:
        return None
    ct = _sub(param, 1)
    if ct is None:
        return None
    code = _first(ct, 1, 0)
    return next((k for k, v in CLEAN_TYPES.items() if v == code), None)
