#!/usr/bin/env python3
"""tonex_bridge.py — MIDI/Ableton → IK TONEX One software bridge (no ESP32).

Chain:  Ableton (or any MIDI source) → IAC bus → this script → USB serial → TONEX One.

MIDI map (see README):
  Program Change 0-19  -> load preset N
  CC 127 (0-19)        -> load preset N        (Ableton-native: CC automation)
  CC 86 / CC 87 (>=64) -> preset down / up
  CC 123 (>=64)        -> bypass toggle
  CC 122 (0-127)       -> global volume (dB scale)
  CC 88  (0-127)       -> BPM (40-240)
  CC 2,5,6,8,18,19,32,37,75,102,103,106,107 -> parameters (tonex.js numbering,
                                                  aligned with Builty MidiCommands)

Requires: .venv with pyserial, mido, python-rtmidi.  Run:
  .venv/bin/python tonex_bridge.py [--scan]
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time

import serial
import serial.tools.list_ports

from tonex_proto import (
    BAUD, MAX_PRESETS, PARAM_DEFS, PID, VID,
    active_idx, crc16, frame, hello, load_preset_patch, parse_state,
    patch_global, req_state, send_mvol, send_param, set_state, state_info,
    unframe, cc_to_value,
)

# Builty MidiCommands-aligned param CCs:  CC -> (param idx, label)
DEFAULT_PARAM_CC: dict[int, tuple[int, str]] = {
    2: (95, "DLY POWER"),        # /127 on
    5: (99, "DG TIME"),          # 0-2000 ms
    6: (100, "DG FBACK"),        # 0-100
    8: (102, "DG MIX"),          # 0-100
    18: (6, "COMP POWER"),       # /127 on
    19: (7, "COMP THRESH"),      # -40..0 dB
    32: (64, "MOD POWER"),       # /127 on
    37: (70, "CH LEVEL"),        # 0-100
    75: (37, "RVB POWER"),       # /127 on
    102: (20, "MDL GAIN"),       # 0-10
    103: (21, "MDL VOL"),        # 0-10
    106: (34, "MDL PRE"),        # 0-10
    107: (35, "MDL DEP"),        # 0-10
}

# special CCs (do not collide with DEFAULT_PARAM_CC above)
CC_SELECT = 127      # 0-19 -> load preset
CC_PRESET_DOWN = 86
CC_PRESET_UP = 87
CC_BPM = 88
CC_GLOBAL_VOL = 122
CC_BYPASS = 123


class TonexDevice:
    """USB serial link to the TONEX One + background frame parser."""

    def __init__(self, port: str, verbose: bool = False):
        self.port = port
        self.verbose = verbose
        self.ser: serial.Serial | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.state: bytes | None = None
        self.reader: threading.Thread | None = None

    # ---- lifecycle ----------------------------------------------------
    def open(self) -> None:
        self.ser = serial.Serial(port=self.port, baudrate=BAUD, bytesize=serial.EIGHTBITS,
                                 parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                 timeout=0.05, write_timeout=2.0)
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name="tonex-reader")
        self.reader.start()

    def close(self) -> None:
        self._stop.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    # ---- low level ----------------------------------------------------
    def write(self, data: bytes) -> None:
        with self._lock:
            if self.ser:
                self.ser.write(data)

    def _read_loop(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            ser = self.ser
            if ser is None:
                return
            try:
                chunk = ser.read(512)
            except Exception:
                if self._stop.is_set():
                    return
                continue
            if not chunk:
                continue
            buf += chunk
            while True:
                s = buf.find(0x7E)
                if s < 0:
                    buf = bytearray()
                    break
                if s > 0:
                    buf = buf[s:]
                e = buf.find(0x7E, 1)
                if e < 0:
                    break
                msg = bytes(buf[: e + 1])
                buf = buf[e + 1:]
                payload = unframe(msg)
                if not payload:
                    continue
                sd = parse_state(payload)
                if sd:
                    self.state = sd
                    if self.verbose:
                        info = state_info(sd)
                        print(f"  [state] len={len(sd)} slotA={info['slot_a']} "
                              f"slotB={info['slot_b']} cur={info['current_slot']} "
                              f"active={info['active']} bypass={info['bypass']}")

    def wait_for_state(self, timeout: float = 6.0) -> bool:
        self.write(hello())
        time.sleep(0.25)
        self.write(req_state())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.state is None:
            time.sleep(0.05)
        return self.state is not None

    # ---- commands -----------------------------------------------------
    def load_preset(self, n: int, toggle_on_repeat: bool = False) -> str | None:
        """Switch to preset n. Returns a human log line or None when skipped."""
        if self.state is None:
            return None
        patched = load_preset_patch(self.state, n, toggle_on_repeat)
        if patched is None:
            return f"preset {n} already active (skip)"
        self.write(set_state(patched))
        self.state = patched
        return f"preset -> {n}"

    def set_param(self, idx: int, value: float) -> str | None:
        """Write parameter idx (0-108 per-preset, 110-116 global)."""
        if 0 <= idx <= 108:
            self.write(send_param(idx, value))
            return f"param {idx} {PARAM_DEFS[idx][0]} = {value:.2f}"
        if idx == 116:  # global volume, dB
            self.write(send_mvol(((value + 40.0) / 43.0) * 10.0))
            return f"global volume = {value:.1f} dB"
        if self.state is None:
            return None
        patched = patch_global(self.state, idx, value)
        if patched is None:
            return None
        self.write(set_state(patched))
        self.state = patched
        return f"global {idx} {PARAM_DEFS[idx][0]} = {value:.2f}"

    def preset_step(self, delta: int) -> str | None:
        if self.state is None:
            return None
        n = active_idx(self.state) + delta
        n = max(0, min(MAX_PRESETS - 1, n))
        return self.load_preset(n)

    def toggle_bypass(self) -> str | None:
        if self.state is None:
            return None
        sd = bytearray(self.state)
        sd[-12] = 1 - sd[-12]
        sd[-7] = 1
        self.write(set_state(bytes(sd)))
        self.state = bytes(sd)
        return "bypass toggle"


def find_tonex_port() -> str | None:
    for p in serial.tools.list_ports.comports():
        if p.vid == VID and p.pid == PID:
            return p.device
    return None


def find_midi_in(name_hint: str | None) -> str | None:
    import mido
    names = mido.get_input_names()
    if name_hint:
        for n in names:
            if name_hint.lower() in n.lower():
                return n
        return None
    for n in names:
        if "iac" in n.lower():
            return n
    return names[0] if names else None


def cc_to_param_value(param_idx: int, cc: int) -> float:
    return cc_to_value(param_idx, cc)


def main() -> int:
    ap = argparse.ArgumentParser(description="TONEX One MIDI bridge (Ableton → USB serial)")
    ap.add_argument("--serial", help="serial device (default: auto-detect VID 1963 PID 00D1)")
    ap.add_argument("--midi-in", help="MIDI input port name substring (default: first IAC)")
    ap.add_argument("--channel", type=int, default=None, help="only listen on MIDI channel 1-16")
    ap.add_argument("--param-map", help="JSON file overriding param CC map: {\"CC\": param_idx}")
    ap.add_argument("--scan", action="store_true", help="list serial + MIDI ports and exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.scan:
        print("--- serial ---")
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            print("  (none)")
        for p in ports:
            vid = f"{p.vid:04X}" if p.vid is not None else "----"
            pid = f"{p.pid:04X}" if p.pid is not None else "----"
            mark = "  <-- TONEX" if (p.vid == VID and p.pid == PID) else ""
            print(f"  {p.device}  {vid}:{pid}  {p.description}{mark}")
        print("--- MIDI in ---")
        try:
            import mido
            names = mido.get_input_names()
            print("  " + ("\n  ".join(names) if names else "(none)"))
        except Exception as e:  # noqa: BLE001
            print(f"  mido error: {e}")
        return 0

    param_cc = dict(DEFAULT_PARAM_CC)
    if args.param_map:
        with open(args.param_map) as f:
            data = json.load(f)
        for k, v in data.items():
            param_cc[int(k)] = (int(v), PARAM_DEFS[int(v)][0])

    port = args.serial or find_tonex_port()
    if not port:
        sys.stderr.write("TONEX One not found on USB. Plug it in and re-run (or use --scan).\n")
        return 1

    try:
        import mido
    except ImportError:
        sys.stderr.write("mido/python-rtmidi missing — run: .venv/bin/pip install mido python-rtmidi\n")
        return 1

    midi_name = find_midi_in(args.midi_in)
    if not midi_name:
        sys.stderr.write("No MIDI input found. Enable IAC Driver in Audio MIDI Setup, "
                         "then re-run. Ports: " + ", ".join(mido.get_input_names()) + "\n")
        return 1

    dev = TonexDevice(port, verbose=args.verbose)
    try:
        dev.open()
        print(f"serial  : {port}")
        print(f"midi in : {midi_name}" + (f"  (channel {args.channel})" if args.channel else " (any)"))
        print(f"syncing with pedal...")
        if not dev.wait_for_state():
            sys.stderr.write("No state response from pedal. Is it powered and in pedal mode?\n")
            return 1
        info = state_info(dev.state)
        print(f"ready   : active preset {info['active']} (slot {'ABC'[info['current_slot']]})"
              f"  bpm {info['bpm']:.0f}  bypass {info['bypass']}")
        print("--- bindings ---")
        print(f"  PC 0-19 | CC {CC_SELECT} 0-19   -> load preset")
        print(f"  CC {CC_PRESET_DOWN}/{CC_PRESET_UP}             -> preset down/up")
        print(f"  CC {CC_BYPASS}                -> bypass toggle")
        print(f"  CC {CC_GLOBAL_VOL}               -> global volume")
        print(f"  CC {CC_BPM}                -> BPM")
        for cc, (idx, name) in sorted(param_cc.items()):
            print(f"  CC {cc:<3}                -> {name} (param {idx})")
        print("---")

        cmd_q: queue.Queue = queue.Queue()

        def on_msg(msg):
            cmd_q.put(msg)

        with mido.open_input(midi_name, callback=on_msg) as _:
            while True:
                try:
                    msg = cmd_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if args.channel and getattr(msg, "channel", None) is not None:
                    if msg.channel + 1 != args.channel:
                        continue
                line = None
                if msg.type == "program_change":
                    n = msg.program
                    if 0 <= n < MAX_PRESETS:
                        line = dev.load_preset(n)
                    else:
                        line = f"PC {n} out of range"
                elif msg.type == "control_change":
                    c, v = msg.control, msg.value
                    if c == CC_SELECT:
                        line = dev.load_preset(v) if v < MAX_PRESETS else f"CC127 {v} out of range"
                    elif c == CC_PRESET_DOWN and v >= 64:
                        line = dev.preset_step(-1)
                    elif c == CC_PRESET_UP and v >= 64:
                        line = dev.preset_step(+1)
                    elif c == CC_BYPASS and v >= 64:
                        line = dev.toggle_bypass()
                    elif c == CC_GLOBAL_VOL:
                        line = dev.set_param(116, -40.0 + 43.0 * (v / 127.0))
                    elif c == CC_BPM:
                        line = dev.set_param(110, 40.0 + 200.0 * (v / 127.0))
                    elif c in param_cc:
                        idx, _ = param_cc[c]
                        line = dev.set_param(idx, cc_to_param_value(idx, v))
                if line:
                    ts = time.strftime("%H:%M:%S")
                    src = getattr(msg, "type", "?")
                    print(f"[{ts}] {src} -> {line}", flush=True)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())