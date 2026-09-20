"""tonex_proto.py — TONEX One USB protocol layer (pure Python, no hardware).

Byte-exact port of the protocol implemented in three independent projects:
  - tonex-one-control/tonex.js   (WebSerial browser editor, mdegani)
  - PyTonexControl               (Python package, MKlimenko)
  - Builty/TonexOneController    (ESP32-S3 firmware)

Transport: serial 115200 8N1.  HDLC framing (FLAG 0x7E / ESC 0x7D, byte^0x20),
CRC-16 (poly 0x8408 reflected, init 0xFFFF, xorout 0xFFFF, appended LE).

State message layout (cross-validated):
  payload  = [16-byte header][state block]
  header   = B9 03 81 06 03 82 <lenLE> 80 0B 03 B9 02 81 06 03 0B
  state len N = payload[6];  state = payload[-N:]   (PyTonexControl: payload[(len-N):])
"""

from __future__ import annotations

import struct

VID = 0x1963
PID = 0x00D1
BAUD = 115200
MAX_PRESETS = 20

HDLC_FLAG = 0x7E
HDLC_ESC = 0x7D
HDLC_MASK = 0x20

# marker of the preset-name block inside a preset-detail response (tonex.js _PM)
PRESET_NAME_MARKER = bytes([0xB9, 0x04, 0xB9, 0x02, 0xBC, 0x21])

# ---- state offsets: SO_* from start, SE_* from END -------------------------
SO_TRIM = 15          # float32 LE
SO_CAB = 20           # byte
SE_BPM = 4            # float32 LE
SE_TEMPO = 6          # byte
SE_DMON = 7           # byte (direct monitoring, set 1 on every state write)
SE_TUNE = 9           # uint16 LE (Hz)
SE_SLOT = 11          # byte: current slot 0=A, 1=B, 2=C
SE_BYP = 12           # byte: bypass flag
SE_SC = 14            # byte: preset index in slot C
SE_SB = 16            # byte: preset index in slot B
SE_SA = 18            # byte: preset index in slot A

_MIN_STATE_LEN = 24


def crc16(data: bytes) -> int:
    """CRC-16-CCITT reflected (poly 0x8408), init 0xFFFF, xorout 0xFFFF."""
    c = 0xFFFF
    for byte in data:
        c ^= byte
        for _ in range(8):
            c = ((c >> 1) ^ 0x8408) if (c & 1) else (c >> 1)
    return (c ^ 0xFFFF) & 0xFFFF


def frame(data: bytes) -> bytes:
    """HDLC-frame a payload: wrap in FLAGs, escape 0x7E/0x7D, append CRC LE."""
    out = bytearray([HDLC_FLAG])
    for b in data:
        if b in (HDLC_FLAG, HDLC_ESC):
            out += bytes((HDLC_ESC, b ^ HDLC_MASK))
        else:
            out.append(b)
    c = crc16(data)
    for b in (c & 0xFF, (c >> 8) & 0xFF):
        if b in (HDLC_FLAG, HDLC_ESC):
            out += bytes((HDLC_ESC, b ^ HDLC_MASK))
        else:
            out.append(b)
    out.append(HDLC_FLAG)
    return bytes(out)


def unframe(buf: bytes) -> bytes | None:
    """Unframe one complete message; returns payload or None if invalid."""
    if len(buf) < 4 or buf[0] != HDLC_FLAG or buf[-1] != HDLC_FLAG:
        return None
    u = bytearray()
    i, e = 1, len(buf) - 1
    while i < e:
        if buf[i] == HDLC_ESC:
            i += 1
            if i >= e:
                return None
            u.append(buf[i] ^ HDLC_MASK)
        else:
            u.append(buf[i])
        i += 1
    if len(u) < 2:
        return None
    payload = bytes(u[:-2])
    rc = u[-2] | (u[-1] << 8)
    return payload if rc == crc16(payload) else None


def _f32(v: float) -> bytes:
    return struct.pack("<f", float(v))


# ---- message builders -------------------------------------------------------
def hello() -> bytes:
    return frame(bytes([0xB9, 0x03, 0x00, 0x82, 0x04, 0x00, 0x80, 0x0B, 0x01,
                        0xB9, 0x02, 0x02, 0x0B]))


def req_state() -> bytes:
    return frame(bytes([0xB9, 0x03, 0x00, 0x82, 0x06, 0x00, 0x80, 0x0B, 0x03,
                        0xB9, 0x02, 0x81, 0x06, 0x03, 0x0B]))


def req_mvol() -> bytes:
    return frame(bytes([0xB9, 0x03, 0x81, 0x0D, 0x03, 0x82, 0x05, 0x00, 0x80, 0x0B, 0x03,
                        0xB9, 0x03, 0x03, 0x00, 0x00]))


def req_preset(idx: int, full: bool = False) -> bytes:
    m = bytearray([0xB9, 0x03, 0x81, 0x00, 0x03, 0x82, 0x06, 0x00, 0x80, 0x0B, 0x03,
                   0xB9, 0x04, 0x0B, 0x01, 0x00, 0x00])
    m[15] = idx & 0xFF
    m[16] = 1 if full else 0
    return frame(bytes(m))


def set_state(sd: bytes) -> bytes:
    ln = len(sd)
    h = bytes([0xB9, 0x03, 0x81, 0x06, 0x03, 0x82, ln & 0xFF, (ln >> 8) & 0xFF,
               0x80, 0x0B, 0x03])
    return frame(h + sd)


def send_param(idx: int, value: float) -> bytes:
    """Write one per-preset parameter (0-based index, real units)."""
    h = bytearray([0xB9, 0x03, 0x81, 0x09, 0x03, 0x82, 0x0A, 0x00, 0x80, 0x0B, 0x03])
    p = bytearray([0xB9, 0x04, 0x02, 0x00, idx & 0xFF, 0x88, 0, 0, 0, 0])
    p[6:10] = _f32(value)
    return frame(bytes(h + p))


def send_mvol(internal: float) -> bytes:
    """Global volume; internal range 0..10 (dB -40..+3 mapped by caller)."""
    h = bytearray([0xB9, 0x03, 0x81, 0x09, 0x03, 0x82, 0x0A, 0x00, 0x80, 0x0B, 0x03])
    p = bytearray([0xB9, 0x04, 0x03, 0x00, 0x00, 0x88, 0, 0, 0, 0])
    p[6:10] = _f32(internal)
    return frame(bytes(h + p))


# ---- state handling ---------------------------------------------------------
def parse_state(payload: bytes) -> bytes | None:
    """Extract the state block from a response payload, or None if not a state msg."""
    if len(payload) < 8 or payload[0] != 0xB9:
        return None
    n = payload[6]
    if n <= 0 or n > len(payload):
        return None
    hdr = len(payload) - n
    if not (8 <= hdr <= 32):
        return None
    sd = payload[-n:]
    if len(sd) < _MIN_STATE_LEN:
        return None
    return sd


def parse_preset_name(payload: bytes) -> str | None:
    """Extract the 32-byte preset name from a preset-detail response.

    Port of tonex.js _parseName: find the marker, read up to 32 bytes,
    cut at the first NUL, decode and trim. None when no marker/empty.
    """
    i = payload.find(PRESET_NAME_MARKER)
    if i < 0:
        return None
    raw = payload[i + len(PRESET_NAME_MARKER): i + len(PRESET_NAME_MARKER) + 32]
    if not raw:
        return None
    end = raw.find(0)
    if end >= 0:
        raw = raw[:end]
    name = raw.decode("utf-8", errors="replace").strip()
    return name or None


def active_idx(sd: bytes) -> int:
    """Index (0-19) of the currently active preset."""
    cur = sd[-SE_SLOT]
    if cur == 0:
        return sd[-SE_SA]
    if cur == 1:
        return sd[-SE_SB]
    return sd[-SE_SC]


def state_info(sd: bytes) -> dict:
    return {
        "slot_a": sd[-SE_SA],
        "slot_b": sd[-SE_SB],
        "slot_c": sd[-SE_SC],
        "current_slot": sd[-SE_SLOT],
        "bypass": sd[-SE_BYP],
        "active": active_idx(sd),
        "bpm": struct.unpack("<f", sd[-SE_BPM:])[0],
        "tempo_src": sd[-SE_TEMPO],
        "tuneref": sd[-SE_TUNE] | (sd[-SE_TUNE + 1] << 8),
        "trim": struct.unpack("<f", sd[SO_TRIM:SO_TRIM + 4])[0],
        "cabsim": sd[SO_CAB],
    }


def set_slot_patch(sd: bytes, slot: int, n: int) -> bytes | None:
    """Load preset n into slot A/B/C (0/1/2) without switching the active slot.

    Returns patched state, or None when the slot already holds n.
    When `slot` == current slot, the pedal switches to n (set_state triggers it).
    """
    if not (0 <= slot <= 2) or not (0 <= n < MAX_PRESETS):
        raise ValueError("slot/name out of range")
    patch = bytearray(sd)
    off = (-SE_SA, -SE_SB, -SE_SC)[slot]
    if patch[off] == n:
        return None
    patch[off] = n
    patch[-SE_DMON] = 1
    return bytes(patch)


def toggle_slot_patch(sd: bytes) -> bytes:
    """Flip the current slot A<->B — mirrors the pedal footswitch, no preset reload."""
    patch = bytearray(sd)
    cur = patch[-SE_SLOT]
    if cur in (0, 1):
        patch[-SE_SLOT] = 1 - cur
    patch[-SE_BYP] = 0
    patch[-SE_DMON] = 1
    return bytes(patch)


def load_preset_patch(sd: bytes, n: int, toggle_on_repeat: bool = False) -> bytes | None:
    """Return the state patched to make preset n active, or None if it is a no-op.

    Mirrors tonex.js _doSetPreset but with a safe default: re-selecting the
    already-active preset does NOT toggle bypass (Ableton clip loops re-send
    the same PC/CC values). Pass toggle_on_repeat=True for editor behaviour.
    """
    if not (0 <= n < MAX_PRESETS):
        raise ValueError(f"preset index {n} out of range 0..{MAX_PRESETS - 1}")
    patch = bytearray(sd)
    cur = sd[-SE_SLOT]
    active = active_idx(sd)
    bypass = sd[-SE_BYP]
    if n == active:
        if bypass == 0 and not toggle_on_repeat:
            return None                      # already active, not bypassed: no-op
        patch[-SE_BYP] = 1 if (bypass == 0 and toggle_on_repeat) else 0
    else:
        patch[-SE_BYP] = 0
        if cur == 0:
            patch[-SE_SA] = n
        elif cur == 1:
            patch[-SE_SB] = n
        else:
            patch[-SE_SC] = n
    patch[-SE_DMON] = 1
    return bytes(patch)


_GLOBAL_PATCH = {110: ("bpm", "f32end"), 111: ("trim", "f32@15"), 112: ("cabsim", "b@20"),
                 113: ("tempos", "b@end6"), 114: ("tuneref", "u16end9"), 115: ("bypass", "b@end12")}


def patch_global(sd: bytes, idx: int, value: float) -> bytes | None:
    """Return state patched for a global param (110-115), or None for 0-108/116.

    Global params live in the state block, so they are written via set_state.
    """
    if idx not in _GLOBAL_PATCH:
        return None
    patch = bytearray(sd)
    if idx == 110:      # BPM
        patch[-SE_BPM:] = _f32(value)
    elif idx == 111:    # input trim
        patch[SO_TRIM:SO_TRIM + 4] = _f32(value)
    elif idx == 112:    # cab sim bypass
        patch[SO_CAB] = int(value)
    elif idx == 113:    # tempo source (global/local)
        patch[-SE_TEMPO] = int(value)
    elif idx == 114:    # tuning reference, uint16 LE (Hz)
        r = int(value) & 0xFFFF
        patch[-SE_TUNE] = r & 0xFF
        patch[-SE_TUNE + 1] = (r >> 8) & 0xFF
    elif idx == 115:    # bypass
        patch[-SE_BYP] = int(value)
    patch[-SE_DMON] = 1
    return bytes(patch)


# ---- parameter reference (tonex.js PARAM_DEFS, 0-based, real units) --------
# bool = 0/1 toggle written with threshold >= 64 when coming from a MIDI CC.
PARAM_DEFS: dict[int, tuple[str, float, float]] = {
    0: ("NG POST", 0, 1), 1: ("NG POWER", 0, 1), 2: ("NG THRESH", -100, 0),
    3: ("NG REL", 5, 500), 4: ("NG DEPTH", -100, -20), 5: ("COMP POST", 0, 1),
    6: ("COMP POWER", 0, 1), 7: ("COMP THRESH", -40, 0), 8: ("COMP GAIN", -30, 10),
    9: ("COMP ATTACK", 1, 51), 10: ("EQ POST", 0, 1), 11: ("EQ BASS", 0, 10),
    12: ("EQ BFREQ", 75, 600), 13: ("EQ MID", 0, 10), 14: ("EQ MIDQ", 0.2, 3.0),
    15: ("EQ MFREQ", 150, 5000), 16: ("EQ TREBLE", 0, 10), 17: ("EQ TFREQ", 1000, 4000),
    18: ("MDL AMP", 0, 1), 19: ("MDL SW1", 0, 1), 20: ("MDL GAIN", 0, 10),
    21: ("MDL VOL", 0, 10), 22: ("MDL MIX", 0, 100), 23: ("MDL CABU", 0, 1),
    24: ("MDL CAB", 0, 2), 25: ("VIR CMDL", 0, 39), 26: ("VIR RESO", 0, 10),
    27: ("VIR M1", 0, 2), 28: ("VIR M1X", 0, 10), 29: ("VIR M1Z", 0, 10),
    30: ("VIR M2", 0, 2), 31: ("VIR M2X", 0, 2), 32: ("VIR M2Z", 0, 10),
    33: ("VIR BLEND", -100, 100), 34: ("MDL PRE", 0, 10), 35: ("MDL DEP", 0, 10),
    36: ("RVB POS", 0, 1), 37: ("RVB POWER", 0, 1), 38: ("RVB MODEL", 0, 5),
    39: ("RVB SP1 TIME", 0, 10), 40: ("RVB SP1 PDLY", 0, 200), 41: ("RVB SP1 CLR", 0, 10),
    42: ("RVB SP1 MIX", 0, 100), 43: ("RVB SP2 TIME", 0, 10), 44: ("RVB SP2 PDLY", 0, 200),
    45: ("RVB SP2 CLR", 0, 10), 46: ("RVB SP2 MIX", 0, 100), 47: ("RVB SP3 TIME", 0, 10),
    48: ("RVB SP3 PDLY", 0, 200), 49: ("RVB SP3 CLR", 0, 10), 50: ("RVB SP3 MIX", 0, 100),
    51: ("RVB SP4 TIME", 0, 10), 52: ("RVB SP4 PDLY", 0, 200), 53: ("RVB SP4 CLR", 0, 10),
    54: ("RVB SP4 MIX", 0, 100), 55: ("RVB RM TIME", 0, 10), 56: ("RVB RM PDLY", 0, 200),
    57: ("RVB RM CLR", 0, 10), 58: ("RVB RM MIX", 0, 100), 59: ("RVB PL TIME", 0, 10),
    60: ("RVB PL PDLY", 0, 200), 61: ("RVB PL CLR", 0, 10), 62: ("RVB PL MIX", 0, 100),
    63: ("MOD POST", 0, 1), 64: ("MOD POWER", 0, 1), 65: ("MOD MODEL", 0, 4),
    66: ("CH SYNC", 0, 1), 67: ("CH TS", 0, 14), 68: ("CH RATE", 0, 10),
    69: ("CH DEPTH", 0, 10), 70: ("CH LEVEL", 0, 100), 71: ("TR SYNC", 0, 1),
    72: ("TR TS", 0, 14), 73: ("TR RATE", 0, 10), 74: ("TR SHAPE", 0, 10),
    75: ("TR SPREAD", 0, 10), 76: ("TR LEVEL", 0, 100), 77: ("PH SYNC", 0, 1),
    78: ("PH TS", 0, 14), 79: ("PH RATE", 0, 10), 80: ("PH DEPTH", 0, 10),
    81: ("PH LEVEL", 0, 100), 82: ("FL SYNC", 0, 1), 83: ("FL TS", 0, 14),
    84: ("FL RATE", 0, 10), 85: ("FL DEPTH", 0, 10), 86: ("FL FBACK", 0, 10),
    87: ("FL LEVEL", 0, 100), 88: ("RT SYNC", 0, 1), 89: ("RT TS", 0, 14),
    90: ("RT SPEED", 0, 10), 91: ("RT RADIUS", 0, 10), 92: ("RT SPREAD", 0, 10),
    93: ("RT LEVEL", 0, 100), 94: ("DLY POST", 0, 1), 95: ("DLY POWER", 0, 1),
    96: ("DLY MODEL", 0, 1), 97: ("DG SYNC", 0, 1), 98: ("DG TS", 0, 14),
    99: ("DG TIME", 0, 2000), 100: ("DG FBACK", 0, 100), 101: ("DG MODE", 0, 2),
    102: ("DG MIX", 0, 100), 103: ("TP SYNC", 0, 1), 104: ("TP TS", 0, 14),
    105: ("TP TIME", 0, 2000), 106: ("TP FBACK", 0, 100), 107: ("TP MODE", 0, 2),
    108: ("TP MIX", 0, 100),
    110: ("BPM", 40, 240), 111: ("TRIM", -15, 15), 112: ("CABSIM", 0, 1),
    113: ("TEMPOS", 0, 1), 114: ("TUNEREF", 415, 465), 115: ("BYPASS", 0, 1),
    116: ("MVOL", -40, 3),
}


def cc_to_value(param_idx: int, cc: int) -> float:
    """Map a 0-127 MIDI CC value into a parameter's real-unit range."""
    name, mn, mx = PARAM_DEFS[param_idx]
    if (mn, mx) == (0, 1):
        return 1.0 if cc >= 64 else 0.0
    return mn + (mx - mn) * (cc / 127.0)