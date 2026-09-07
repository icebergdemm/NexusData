"""Live extraction from the demo binary stream (routes/replays.py).

Covers the WorldDemoStreamer v6 contract: despawn frame 0x04, voice frame
0x09 with channel marker, chat 0x05, static geometry 0x10/0x11, truncation
safety, and despawn application to the live cache.

Runnable directly (python tests/test_replay_stream_live.py) and under pytest.
"""

import importlib.util
import struct
import sys
import os
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_replays_module():
    """Import routes.replays without executing routes/__init__ (which pulls
    unrelated heavy deps like aiofiles that may be missing locally)."""
    try:
        from routes.replays import (  # noqa: F401
            _parse_demo_chunk,
            _apply_parsed_to_state,
            _fill_static_from_demo,
        )
        import routes.replays as mod
        return mod
    except ModuleNotFoundError:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "routes", "replays.py")
        spec = importlib.util.spec_from_file_location("replays_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["replays_under_test"] = mod
        spec.loader.exec_module(mod)
        return mod


_mod = _load_replays_module()
_parse_demo_chunk = _mod._parse_demo_chunk
_apply_parsed_to_state = _mod._apply_parsed_to_state
_fill_static_from_demo = _mod._fill_static_from_demo


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def _header(version=6, server="ASPER1", seed="abc", level="Ridge", sub="Orca") -> bytes:
    out = b"BDMO"
    out += struct.pack("<H", version) + struct.pack("<H", 1) + struct.pack("B", 10)
    out += struct.pack("<q", 1753000000000)
    out += _str(server) + _str(seed) + _str(level) + _str(sub)
    out += struct.pack("<I", 0)
    return out


def _origin(x=1000, y=-2000) -> bytes:
    return bytes([0x02]) + struct.pack("<ii", x, y)


def _spawn(t=100, cid=3, species="human", name="Ivan", team=1) -> bytes:
    return (bytes([0x03]) + struct.pack("<I", t) + struct.pack("<H", cid)
            + _str(species) + _str(name) + struct.pack("b", team))


def _despawn(t=200, cid=3) -> bytes:
    return bytes([0x04]) + struct.pack("<I", t) + struct.pack("<H", cid)


def _snapshot(t=300, ents=((3, 10, -20, 0x04, 200),), doors=((87, 255),), subs=((1, 1000, -2000),)) -> bytes:
    out = bytes([0x01]) + struct.pack("<I", t) + struct.pack("<H", len(ents))
    for cid, x, y, flags, hp in ents:
        out += struct.pack("<H", cid) + struct.pack("<hhBB", x, y, flags, hp)
    out += struct.pack("<H", len(doors))
    for did, opened in doors:
        out += struct.pack("<H", did) + bytes([opened])
    out += struct.pack("<H", 0)  # hull water count
    out += struct.pack("<H", len(subs))
    for sid, x, y in subs:
        out += struct.pack("<H", sid) + struct.pack("<ii", x, y)
    return out


def _inventory(items=((901, 3, "revolver", 200),)) -> bytes:
    out = bytes([0x08]) + struct.pack("<H", len(items))
    for iid, owner, ident, cond in items:
        out += struct.pack("<H", iid) + struct.pack("<H", owner) + _str(ident) + bytes([cond])
    return out


def _voice09(t=150, speaker=3, radio=1, payload=b"\x01\x02\x03") -> bytes:
    return (bytes([0x09]) + struct.pack("<I", t) + struct.pack("<H", speaker)
            + bytes([radio]) + struct.pack("<H", len(payload)) + payload)


def _voice07(t=150, speaker=3, payload=b"\x01\x02") -> bytes:
    return (bytes([0x07]) + struct.pack("<I", t) + struct.pack("<H", speaker)
            + struct.pack("<H", len(payload)) + payload)


def _event(kind=2, t=120) -> bytes:
    return (bytes([0x06]) + struct.pack("<I", t) + bytes([kind])
            + struct.pack("<HHii f".replace(" ", ""), 3, 0, 100, 200, 1.5) + _str("hit"))


def _chat() -> bytes:
    return bytes([0x05]) + struct.pack("<I", 110) + struct.pack("<H", 3) + bytes([0]) + _str("Ivan") + _str("hi")


def _geometry() -> bytes:
    out = bytes([0x10]) + struct.pack("<H", 1)
    out += struct.pack("<H", 1) + struct.pack("<H", 12) + struct.pack("<iiii", -500, -300, 200, 150)
    out += struct.pack("<H", 1)
    out += struct.pack("<H", 1) + struct.pack("<iiii", -500, -300, 200, 20) + bytes([0x01])
    out += struct.pack("<H", 1)
    out += struct.pack("<H", 1) + struct.pack("<H", 87) + struct.pack("<iiii", -300, -300, 32, 100) + bytes([0x00])
    return out


def _outline() -> bytes:
    out = bytes([0x11]) + struct.pack("<ii", 100000, 50000) + struct.pack("<H", 1)
    out += struct.pack("<H", 3) + struct.pack("<iiiiii", 1000, 2000, 1050, 2000, 1050, 2100)
    return out


def test_snapshot_roundtrip():
    data = _header() + _origin() + _spawn() + _snapshot() + _inventory() + bytes([0xFF]) + struct.pack("<I", 400)
    p = _parse_demo_chunk(data)
    assert p["header"]["version"] == 6, p["header"]
    assert p["header"]["server"] == "ASPER1"
    assert p["spawns"][3]["name"] == "Ivan"
    assert p["t"] == 0.3, p["t"]
    ch = p["characters"][0]
    assert ch["id"] == 3 and ch["human"] is True, ch
    assert ch["x"] == 1000 + 10 * 4 and ch["y"] == -2000 + (-20) * 4, ch
    assert abs(ch["hp"] - 200 * 200.0 / 255.0) < 0.01, ch
    assert p["doors"] == [{"id": 87, "open": 100.0}], p["doors"]
    assert p["subs"] == [{"id": 1, "x": 1000, "y": -2000}], p["subs"]
    assert p["items"][0]["item"] == "revolver" and p["items"][0]["owner_id"] == 3, p["items"]


def test_voice_event_chat_do_not_break_live():
    # Regression: 0x09 used to hit the unknown-frame branch and drop the
    # whole rest of the chunk from live state on every voice-active tick.
    data = _header() + _voice09() + _event() + _chat() + _voice07() + _snapshot() + _inventory()
    p = _parse_demo_chunk(data)
    assert "characters" in p and len(p["characters"]) == 1, p.keys()
    assert "items" in p, p.keys()
    assert p["t"] == 0.3


def test_despawn_applies_to_cache():
    state = {"pending": 0, "seq": 0}
    _apply_parsed_to_state(state, _parse_demo_chunk(_spawn() + _snapshot()))
    assert 3 in state["spawns"] and len(state["characters"]) == 1
    _apply_parsed_to_state(state, _parse_demo_chunk(_despawn()))
    assert 3 not in state.get("spawns", {}), state["spawns"]
    assert state["characters"] == [], state["characters"]


def test_static_geometry_extracted():
    p = _parse_demo_chunk(_header() + _geometry() + _outline())
    st = p["static"]
    assert st["hulls"] == [{"id": 12, "sub": 1, "r": [-500, -300, 200, 150]}], st["hulls"]
    assert st["walls"] == [{"sub": 1, "r": [-500, -300, 200, 20], "h": True, "p": False}], st["walls"]
    assert st["doors"] == [{"id": 87, "sub": 1, "r": [-300, -300, 32, 100], "h": False}], st["doors"]
    assert st["outline"] == [[[1000, 2000], [1050, 2000], [1050, 2100]]], st["outline"]
    assert (st["outline_w"], st["outline_h"]) == (100000, 50000)


def test_truncation_never_raises():
    full = _header() + _geometry() + _outline() + _origin() + _spawn() + _snapshot() + _inventory()
    for cut in (10, 17, 40, len(full) - 5, len(full) - 1):
        p = _parse_demo_chunk(full[:cut])  # must not raise
        assert isinstance(p, dict)


def test_unknown_frame_stops_cleanly():
    data = _snapshot() + bytes([0x42, 0x01, 0x02]) + _snapshot(t=999)
    p = _parse_demo_chunk(data)
    assert p["t"] == 0.3, p.get("t")  # second snapshot after unknown frame ignored


def test_fill_static_only_when_empty():
    parsed = _parse_demo_chunk(_header() + _geometry() + _outline())
    row = SimpleNamespace(hulls=None, walls=[], doors=None, outline=None,
                          outline_w=0, outline_h=0, sub_name="", level_seed=None,
                          level_name=None, submarines=[])
    assert _fill_static_from_demo(row, parsed, {"subs": [{"id": 1}]}) is True
    assert row.hulls[0]["id"] == 12 and row.sub_name == "Orca"
    assert row.level_seed == "abc" and row.outline_w == 100000
    assert row.submarines == [{"id": 1, "name": ""}], row.submarines
    # Second call with other data must not overwrite existing columns.
    assert _fill_static_from_demo(row, {"static": {"hulls": [{"id": 1}]}, "header": {}}, {}) is False
    assert row.hulls[0]["id"] == 12


TESTS = [
    test_snapshot_roundtrip,
    test_voice_event_chat_do_not_break_live,
    test_despawn_applies_to_cache,
    test_static_geometry_extracted,
    test_truncation_never_raises,
    test_unknown_frame_stops_cleanly,
    test_fill_static_only_when_empty,
]


if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e!r}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
