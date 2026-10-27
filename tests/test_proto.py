"""Self-contained tests for the protobuf codec (no Home Assistant needed).

Expected bytes are worked out by hand from the field numbers in Eufy's .proto
files, plus real DPS 152/153 values captured from the Eufy app and an S1 Pro.
Run: python tests/test_proto.py
"""

import base64
import importlib.util
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "custom_components/eufy_clean_rooms/proto.py"
spec = importlib.util.spec_from_file_location("ecr_proto", SRC)
p = importlib.util.module_from_spec(spec)
sys.modules["ecr_proto"] = p
spec.loader.exec_module(p)


def b64(*byte_values: int) -> str:
    return base64.b64encode(bytes(byte_values)).decode()


def delim(body: bytes) -> str:
    return base64.b64encode(p._varint(len(body)) + body).decode()


def test_codec() -> None:
    # 1. Byte-exact with the app's commands to an S1 Pro (DPS 152)
    assert p.mode_ctrl(p.START_AUTO_CLEAN) == "AA=="
    assert p.mode_ctrl(p.RESUME_TASK) == "AggO"
    assert p.mode_ctrl(p.PAUSE_TASK) == "AggN"
    assert p.mode_ctrl(p.START_GOHOME) == "AggG"
    assert p.mode_ctrl(p.STOP_TASK) == b64(2, 0x08, 12)

    # 2. Room clean: method=1, select_rooms_clean(4){Room(1){id,order}..., clean_times=2, map_id=3}
    expected = bytes(
        [0x08, 0x01, 0x22, 16,
         0x0A, 4, 0x08, 3, 0x10, 1,
         0x0A, 4, 0x08, 7, 0x10, 2,
         0x10, 2,
         0x18, 5]
    )
    assert p.select_rooms_clean([3, 7], map_id=5, clean_times=2) == delim(expected)
    assert p.from_b64(p.select_rooms_clean([3, 7], 5, 2)) == expected
    # Large ids / map ids need multi-byte varints
    big = p.parse(p.from_b64(p.select_rooms_clean([300], map_id=1000)))
    rooms_msg = p.parse(big[4][0])
    assert p.parse(rooms_msg[1][0]) == {1: [300], 2: [1]} and rooms_msg[3] == [1000]

    # 3. CleanParamRequest{clean_param(1), area_clean_param(2)} with clean_type(1), mop_mode(4), fan(6)
    param = bytes([0x0A, 2, 0x08, 1, 0x22, 2, 0x08, 2, 0x32, 2, 0x08, 2])
    assert p.clean_param_request("mop", "Turbo", "high") == delim(
        bytes([0x0A, len(param)]) + param + bytes([0x12, len(param)]) + param
    )
    vac = p.parse(p.from_b64(p.clean_param_request("vacuum")))
    assert p.parse(p.parse(vac[2][0])[1][0]) == {1: [0]}  # SWEEP_ONLY sent explicitly
    assert p.parse_clean_type(p.clean_param_request("vacuum_and_mop")) == "vacuum_and_mop"

    # 4a. Room list, UniversalDataResponse{cur_map_room(1){map_id(1), data(2){id(1), name(2), scene(3)}}}
    def room(rid: int, name: str = "", scene: bytes = b"", scene_field: int = 3) -> bytes:
        body = p.f_varint(1, rid)
        if name:
            body += p.f_bytes(2, name.encode())
        if scene:
            body += p.f_bytes(scene_field, scene)
        return body


    kitchen_scene = p.f_varint(1, 4)
    bedroom2_scene = p.f_varint(1, 2) + p.f_bytes(2, p.f_varint(1, 2))
    table = p.f_varint(1, 4) + b"".join(
        p.f_bytes(2, r)
        for r in [room(1, "Living Room"), room(2, scene=kitchen_scene), room(3, scene=bedroom2_scene),
                  room(4, "Kitchen"), room(5, "kitchen"), room(6), room(7, "Café ☕")]
    )
    t = p.parse_room_table(delim(p.f_bytes(1, table)))
    assert t.map_id == 4
    assert [(r.id, r.name) for r in t.rooms] == [
        (1, "Living Room"), (2, "Kitchen"), (3, "Bedroom 2"), (4, "Kitchen (2)"),
        (5, "kitchen (3)"), (6, "Room 6"), (7, "Café ☕")], t.rooms

    # 4b. RoomParams{rooms(2){id(1), name(2), scene(4)}, map_id(3)}
    rp = p.f_bytes(2, room(11, "Office")) + p.f_bytes(2, room(12, scene=kitchen_scene, scene_field=4)) + p.f_varint(3, 9)
    t = p.parse_room_table(delim(rp))
    assert t.map_id == 9 and [(r.id, r.name) for r in t.rooms] == [(11, "Office"), (12, "Kitchen")], t

    assert p.parse_room_table("not base64!!") is None

    # 5. WorkStatus: mode(1){value}, state(2), charging(3){state}, cleaning(6){state}
    ws = p.parse_work_status(b64(4, 0x0A, 0x00, 0x10, 0x05))  # real S1 Pro "cleaning" capture
    assert (ws.state, ws.mode, ws.paused) == (5, 0, False)
    ws = p.parse_work_status(delim(p.f_bytes(1, p.f_varint(1, 1)) + p.f_varint(2, 5) + p.f_bytes(6, p.f_varint(1, 1))))
    assert (ws.state, ws.mode, ws.paused) == (5, 1, True)
    ws = p.parse_work_status(delim(p.f_varint(2, 3) + p.f_bytes(3, p.f_varint(1, 1))))
    assert ws.state == 3 and ws.charging_state == 1
    ws = p.parse_work_status(delim(p.f_varint(2, 7) + p.f_bytes(8, p.f_varint(1, 0))))
    assert ws.state == 7 and not ws.paused

    # 6. ErrorCode: error(2) packed, warn(3)
    packed = p._varint(2013) + p._varint(7)
    assert p.parse_error_code(delim(p.f_varint(1, 99) + p.f_bytes(2, packed))) == [2013, 7]
    assert p.parse_error_code(delim(p.f_varint(3, 5))) == [5]  # unpacked warn
    assert p.parse_error_code(delim(b"")) == []
    assert p.parse_error_code(0) == []



if __name__ == "__main__":
    test_codec()
    print("all proto tests passed")


# ---------------------------------------------------------------- Tuya-transport additions

proto = p  # the module loaded above


def _rooms_of(value: str) -> dict[int, list]:
    msg = proto.parse(proto.from_b64(value))
    return proto.parse(msg[4][0])


def test_select_rooms_clean_zero_based_order() -> None:
    one_based = proto.select_rooms_clean([2, 1], 1)
    zero_based = proto.select_rooms_clean([2, 1], 1, zero_based_order=True)
    assert one_based != zero_based
    sel = _rooms_of(zero_based)
    rooms = [proto.parse(r) for r in sel[1]]
    assert [proto._first(r, 1, 0) for r in rooms] == [2, 1]
    assert [proto._first(r, 2, 0) for r in rooms] == [0, 1]
    assert proto._first(sel, 3) == 1


def test_select_rooms_clean_keeps_room_zero() -> None:
    sel = _rooms_of(proto.select_rooms_clean([0], 1, zero_based_order=True))
    assert len(sel[1]) == 1


def test_select_rooms_clean_customize_mode() -> None:
    sel = _rooms_of(proto.select_rooms_clean([3], 1, mode=proto.ROOMS_MODE_CUSTOMIZE))
    assert proto._first(sel, 5) == 1
    assert 5 not in _rooms_of(proto.select_rooms_clean([3], 1))


def _p2p_map_object(map_info: bytes) -> bytes:
    """One MapChannelMsg{type=MAP_INFO, map_info} as the P2P map stream delivers it."""
    return base64.b64decode(proto.to_b64(proto.f_varint(1, 0) + proto.f_bytes(2, map_info)))


def test_parse_p2p_room_table() -> None:
    room_params = (
        proto.f_bytes(2, proto.f_varint(1, 1) + proto.f_bytes(2, b"Laundry room"))
        + proto.f_bytes(2, proto.f_varint(1, 0))
        + proto.f_varint(3, 1)
    )
    map_info = proto.f_varint(2, 1) + proto.f_varint(8, 4) + proto.f_bytes(12, room_params)
    table = proto.parse_p2p_room_table(_p2p_map_object(map_info))
    assert table.map_id == 1
    assert [(r.id, r.name) for r in table.rooms] == [(1, "Laundry room"), (0, "Room 0")]


def test_parse_p2p_room_table_ignores_outline() -> None:
    map_info = proto.f_varint(2, 1) + proto.f_varint(8, 1)
    assert proto.parse_p2p_room_table(_p2p_map_object(map_info)) is None
    assert proto.parse_p2p_room_table(b"\x00") is None
    assert proto.parse_p2p_room_table(b"") is None


def test_map_edit_rooms_custom() -> None:
    value = proto.map_edit_rooms_custom([2, 5], 1, "mop", "Turbo", "high", 2)
    msg = proto.parse(proto.from_b64(value))
    assert proto._first(msg, 1) == 5 and proto._first(msg, 2) == 1 and proto._first(msg, 3) == 1
    custom = proto._sub(msg, 10)
    order = proto._sub(custom, 2)
    assert [proto._first(proto.parse(r), 1) for r in order[1]] == [2, 5]
    parm = proto._sub(custom, 3)
    room = proto.parse(parm[1][0])
    c = proto._sub(room, 2)
    assert proto._first(proto._sub(c, 1), 1) == proto.CLEAN_TYPES["mop"]
    assert proto._first(proto._sub(c, 2), 1) == proto.SUCTIONS["Turbo"]
    assert proto._first(proto._sub(c, 3), 1) == proto.MOP_LEVELS["high"]
    assert proto._first(c, 5) == 2
