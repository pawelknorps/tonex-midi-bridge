"""test_proto.py — byte-exact protocol tests for tonex_proto.py.

Ground truth sources:
  - PyTonexControl test vector (send_param(1, 1.0))
  - tonex.js generated via scripts/gen_vectors.js (node, vm harness)
  - JS _doSetPreset patched-state outputs (slot A/B + repeat-toggle cases)

Run: .venv/bin/python -m pytest test_proto.py -q   (or: python test_proto.py)
"""

import struct

from tonex_proto import (
    MAX_PRESETS, active_idx, crc16, frame, hello, load_preset_patch, parse_state,
    patch_global, req_mvol, req_preset, req_state, send_mvol, send_param, set_state,
    state_info, unframe, cc_to_value,
)

JS = {
    "crc_010203": 40251,
    "hello": "7e b9 03 00 82 04 00 80 0b 01 b9 02 02 0b 17 8c 7e",
    "req_state": "7e b9 03 00 82 06 00 80 0b 03 b9 02 81 06 03 0b 44 66 7e",
    "req_mvol": "7e b9 03 81 0d 03 82 05 00 80 0b 03 b9 03 03 00 00 e2 f7 7e",
    "req_preset_4": "7e b9 03 81 00 03 82 06 00 80 0b 03 b9 04 0b 01 04 00 a8 40 7e",
    "req_preset_4_full": "7e b9 03 81 00 03 82 06 00 80 0b 03 b9 04 0b 01 04 01 21 51 7e",
    "send_param_20_5": "7e b9 03 81 09 03 82 0a 00 80 0b 03 b9 04 02 00 14 88 00 00 a0 40 c6 8f 7e",
    "send_param_42_5_5": "7e b9 03 81 09 03 82 0a 00 80 0b 03 b9 04 02 00 2a 88 00 00 b0 40 25 e4 7e",
    "send_mvol_5": "7e b9 03 81 09 03 82 0a 00 80 0b 03 b9 04 03 00 00 88 00 00 a0 40 65 5c 7e",
    "frame_esc": "7e aa 7d 5e 7d 5d 00 32 52 7e",
}
# synthetic 40-byte state (identical construction in gen_vectors.js)
STATE = bytes([
    0x03, 0x0A, 0x11, 0x18, 0x1F, 0x26, 0x2D, 0x34, 0x3B, 0x42, 0x49, 0x50,
    0x57, 0x5E, 0x65, 0x00, 0x00, 0x00, 0x3F, 0x88, 0x01, 0x96, 0x02, 0xA4,
    0x07, 0xB2, 0x00, 0xC0, 0x00, 0x00, 0xD5, 0xB8, 0x01, 0x00, 0x01, 0xF8,
    0x00, 0x00, 0xF0, 0x42,
])
SET_STATE_FRAME = "7e b9 03 81 06 03 82 28 00 80 0b 03 " + STATE.hex(" ") + " 8f 8f 7e"
PATCH_SLOT_A_2TO5 = ("03 0a 11 18 1f 26 2d 34 3b 42 49 50 57 5e 65 00 00 00 3f 88 01 96 "
                     "05 a4 07 b2 00 c0 00 00 d5 b8 01 01 01 f8 00 00 f0 42")
PATCH_SLOT_B_7TO5 = ("03 0a 11 18 1f 26 2d 34 3b 42 49 50 57 5e 65 00 00 00 3f 88 01 96 "
                     "02 a4 05 b2 00 c0 00 01 d5 b8 01 01 01 f8 00 00 f0 42")
PATCH_REPEAT_TOGGLE = ("03 0a 11 18 1f 26 2d 34 3b 42 49 50 57 5e 65 00 00 00 3f 88 01 96 "
                       "02 a4 07 b2 00 c0 01 00 d5 b8 01 01 01 f8 00 00 f0 42")
# PyTonexControl verified vector: NOISE_GATE_ENABLE (idx 1) = 1.0
PYTONEX_VECTOR = "7e b9 03 81 09 03 82 0a 00 80 0b 03 b9 04 02 00 01 88 00 00 80 3f b2 71 7e"


def hx(b: bytes) -> str:
    return b.hex(" ")


def test_crc_matches_js():
    assert crc16(bytes([0x01, 0x02, 0x03])) == JS["crc_010203"]
    assert crc16(b"") >= 0 and crc16(b"") <= 0xFFFF


def test_builders_match_js():
    pairs = [
        (hello(), JS["hello"]),
        (req_state(), JS["req_state"]),
        (req_mvol(), JS["req_mvol"]),
        (req_preset(4, False), JS["req_preset_4"]),
        (req_preset(4, True), JS["req_preset_4_full"]),
        (send_param(20, 5.0), JS["send_param_20_5"]),
        (send_param(42, 5.5), JS["send_param_42_5_5"]),
        (send_mvol(5.0), JS["send_mvol_5"]),
        (frame(bytes([0xAA, 0x7E, 0x7D, 0x00])), JS["frame_esc"]),
    ]
    for got, want in pairs:
        assert hx(got) == want, f"\n got {hx(got)}\nwant {want}"


def test_send_param_matches_pytonexcontrol_vector():
    assert hx(send_param(1, 1.0)) == PYTONEX_VECTOR


def test_frame_roundtrip_and_escaping():
    payload = bytes([0x01, 0x7E, 0x7D, 0xFF, 0x00, 0x7E])
    assert unframe(frame(payload)) == payload
    greedy = frame(payload)
    assert unframe(greedy) == payload  # idempotent unframe on exact frame
    # corrupted byte -> reject
    bad = bytearray(frame(payload))
    bad[2] ^= 0xFF
    assert unframe(bytes(bad)) is None
    # truncated -> None
    assert unframe(frame(payload)[:-1]) is None


def test_set_state_matches_js():
    assert hx(set_state(STATE)) == SET_STATE_FRAME


def test_state_parse():
    # wrap STATE in the 16-byte response header (len=40 at payload[6])
    payload = bytes([0xB9, 0x03, 0x81, 0x06, 0x03, 0x82, 40, 0, 0x80, 0x0B, 0x03,
                     0xB9, 0x02, 0x81, 0x06, 0x03, 0x0B]) + STATE
    assert parse_state(payload) == STATE
    assert parse_state(hello()) is None
    info = state_info(STATE)
    assert info["active"] == 2
    assert info["slot_a"] == 2 and info["slot_b"] == 7 and info["slot_c"] == 0
    assert info["current_slot"] == 0 and info["bypass"] == 0
    assert abs(info["bpm"] - 120.0) < 0.01
    assert info["tuneref"] == 440
    assert abs(info["trim"] - 0.5) < 0.01
    assert info["cabsim"] == 1
    assert active_idx(STATE) == 2


def test_load_preset_patch_matches_js_doSetPreset():
    # slot A: active 2 -> 5
    assert hx(load_preset_patch(STATE, 5)) == PATCH_SLOT_A_2TO5
    # slot B active (7) -> 5  (currentSlot = 1 -> patch SE_SB)
    sd_b = bytearray(STATE)
    sd_b[-16] = 7   # slotB active
    sd_b[-11] = 1   # currentSlot = B
    assert hx(load_preset_patch(bytes(sd_b), 5)) == PATCH_SLOT_B_7TO5
    # same preset, bypass off -> my safe default: no-op
    assert load_preset_patch(STATE, 2) is None
    # same preset with toggle_on_repeat -> matches JS bypass toggle 0->1
    assert hx(load_preset_patch(STATE, 2, toggle_on_repeat=True)) == PATCH_REPEAT_TOGGLE
    # same preset while bypassed -> un-bypass, no slot change
    sd_x = bytearray(STATE)
    sd_x[-12] = 1
    out = load_preset_patch(bytes(sd_x), 2)
    assert out is not None and out[-12] == 0 and out[-18] == 2


def test_global_patch_routes():
    # BPM
    p = patch_global(STATE, 110, 96.0)
    assert p is not None and struct.unpack("<f", p[-4:])[0] == 96.0 and p[-7] == 1
    # TRIM
    p = patch_global(STATE, 111, -3.0)
    assert p is not None and struct.unpack("<f", p[15:19])[0] == -3.0
    # TUNEREF
    p = patch_global(STATE, 114, 442)
    assert p is not None and (p[-9] | (p[-8] << 8)) == 442
    # BYPASS
    p = patch_global(STATE, 115, 1)
    assert p is not None and p[-12] == 1
    # per-preset / mvol are NOT state patches
    assert patch_global(STATE, 20, 5.0) is None
    assert patch_global(STATE, 116, 0.0) is None


def test_cc_conversions():
    assert cc_to_value(20, 127) == 10.0      # MDL GAIN full
    assert cc_to_value(20, 0) == 0.0
    assert abs(cc_to_value(102, 64) - 100.0 * 64 / 127) < 1e-9   # DG MIX
    assert cc_to_value(95, 64) == 1.0        # DLY POWER threshold on
    assert cc_to_value(95, 1) == 0.0         # off
    assert abs(cc_to_value(7, 127)) < 1e-9                         # COMP THRESH max = 0 dB
    assert abs(cc_to_value(7, 0) + 40.0) < 1e-9                    # COMP THRESH min = -40 dB


def test_preset_range():
    try:
        load_preset_patch(STATE, MAX_PRESETS)
        assert False, "should raise"
    except ValueError:
        pass


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERR  {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)