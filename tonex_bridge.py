#!/usr/bin/env python3
"""tonex_bridge.py — MIDI/Ableton → IK TONEX One software bridge (no ESP32).

Chain:  Ableton (or any MIDI source) → MIDI input → this script → USB serial → TONEX One.

Features (SOTA):
  - virtual MIDI input port "ToneX Bridge" (no IAC setup needed; IAC fallback)
  - preset switching: PC 0-19, CC 127 (0-19), CC 86/87, MIDI notes (--note-base)
  - 20 preset names fetched from the pedal at startup, shown in logs
  - MIDI clock → pedal BPM sync (hysteresis; --no-clock to disable)
  - OSC server (/preset /param /slot /toggle /tap … — Max, touchOSC, phones)
  - A/B slots: mirror the pedal footswitch (loaded A/B presets, CC 124/125/126)
  - MIDI feedback: virtual "ToneX Bridge Out" reports the current preset
  - tap tempo (CC 10), global volume CC 122, bypass CC 123, BPM CC 88, param CC map
  - setlist mode (--setlist / CC 84/85 song next/prev)
  - interactive stdin CLI (preset/param/vol/names/song/…)
  - auto-reconnect when the pedal is unplugged/replugged
  - JSON config file (--config); launchd LaunchAgent installer

MIDI map (Builty MidiCommands-aligned):
  Program Change 0-19          -> load preset N
  CC 127 (0-19)                -> load preset N        (Ableton-native: CC automation)
  CC 86 / CC 87 (>=64)         -> preset down / up
  CC 84 / CC 85 (>=64)         -> song next / prev (setlist mode)
  CC 123 (>=64)                -> bypass toggle
  CC 122 (0-127)               -> global volume (dB scale)
  CC 88  (0-127)               -> BPM (40-240)
  CC 10                        -> tap tempo
  CC 124 (0-19)                -> load preset into slot A
  CC 125 (0-19)                -> load preset into slot B
  CC 126 (>=64)                -> A/B toggle (footswitch mirror)
  CC 2,5,6,8,18,19,32,37,75,102,103,106,107 -> parameters (tonex.js numbering)

Requires: .venv with pyserial, mido, python-rtmidi.  Run:
  .venv/bin/python tonex_bridge.py [--scan] [--list-presets]
"""

from __future__ import annotations

import argparse
import json
import queue
import select
import shlex
import sys
import threading
import time
from types import SimpleNamespace

import serial
import serial.tools.list_ports

from tonex_features import ClockSync, Setlist, TapTempo, note_preset
from tonex_osc import OscServer, decode as osc_decode, encode as osc_encode
from tonex_proto import (
    BAUD, MAX_PRESETS, PARAM_DEFS, PID, VID,
    active_idx, cc_to_value, frame, hello, load_preset_patch, parse_preset_name,
    parse_state, patch_global, req_preset, req_state, send_mvol, send_param,
    set_slot_patch, set_state, state_info, toggle_slot_patch, unframe,
)

VIRTUAL_PORT_NAME = "ToneX Bridge"

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
CC_SELECT = 127
CC_PRESET_DOWN = 86
CC_PRESET_UP = 87
CC_BPM = 88
CC_TAP = 10
CC_GLOBAL_VOL = 122
CC_BYPASS = 123
CC_SLOT_A = 124
CC_SLOT_B = 125
CC_AB = 126

HELP = """commands:
  preset|p <0-19>          load preset
  up | down                preset +/-1
  bypass                   bypass toggle
  vol <0..1>               global volume (0..1 -> -40..+3 dB)
  db <-40..3>              global volume in dB
  param <idx> <value>      write parameter (PARAM_DEFS index, real units)
  bpm <40..240>            set pedal BPM
  tap                      tap tempo
  slot a|b|c <0-19>        load preset into a slot (A/B/C)
  toggle                   A/B toggle (footswitch mirror)
  names                    list preset names
  status                   bridge + pedal status
  song next|prev|goto <i>  setlist navigation
  setlist <file>           load a setlist JSON
  map <cc> <param>         map CC to parameter index
  clock on|off             toggle MIDI clock -> BPM sync
  osc                      show OSC endpoint
  help | quit              this text / exit"""


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
        self.failed = False
        self._names: list[str | None] | None = None
        self._name_queue: "queue.deque[int]" = __import__("collections").deque()
        self.on_preset_change = None   # callable(preset_idx) after a real switch
        self._state_lock = threading.Lock()   # serializes set_state writes + echoes
        self._expect_state = False

    # ---- lifecycle ----------------------------------------------------
    def open(self) -> None:
        self._stop.clear()
        self.failed = False
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
            if not self.ser:
                self.failed = True
                return
            try:
                self.ser.write(data)
            except Exception:
                self.failed = True

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
                time.sleep(0.05)          # unplugged device: avoid busy spin
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
                    if self._expect_state:
                        self._expect_state = False
                    if self.verbose:
                        info = state_info(sd)
                        print(f"  [state] len={len(sd)} slotA={info['slot_a']} "
                              f"slotB={info['slot_b']} cur={info['current_slot']} "
                              f"active={info['active']} bypass={info['bypass']}")
                    continue
                if self._names is not None and self._name_queue:
                    name = parse_preset_name(payload)
                    if name is not None:
                        self._names[self._name_queue.popleft()] = name

    def wait_for_state(self, timeout: float = 6.0) -> bool:
        self.write(hello())
        time.sleep(0.25)
        self.write(req_state())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.state is None:
            time.sleep(0.05)
        return self.state is not None

    def fetch_names(self, gap: float = 0.03, timeout: float = 4.0) -> list[str | None]:
        """Best-effort fetch of all 20 preset names (in-order responses)."""
        self._names = [None] * MAX_PRESETS
        if not self.ser:
            return self._names
        for i in range(MAX_PRESETS):
            self._name_queue.append(i)
            self.write(req_preset(i, False))
            time.sleep(gap)
        deadline = time.monotonic() + timeout
        while self._name_queue and time.monotonic() < deadline:
            time.sleep(0.05)
        self._name_queue.clear()
        return self._names

    # ---- commands -----------------------------------------------------
    def _write_state(self, patched: bytes) -> None:
        """Write a full state patch, paced by the pedal's async reply so a
        burst of writes cannot read a stale state in between (each echo is
        consumed before the next write goes out). Applies optimistically on
        timeout — the local patch is the authority either way."""
        with self._state_lock:
            self._expect_state = True
            self.state = patched
            self.write(set_state(patched))
            deadline = time.monotonic() + 1.0
            while self._expect_state and time.monotonic() < deadline:
                time.sleep(0.005)
            self._expect_state = False

    def load_preset(self, n: int, toggle_on_repeat: bool = False) -> str | None:
        if self.state is None:
            return None
        patched = load_preset_patch(self.state, n, toggle_on_repeat)
        if patched is None:
            return f"preset {n} already active (skip)"
        self._write_state(patched)
        if self.on_preset_change:
            self.on_preset_change(n)
        return f"preset -> {n}"

    def set_param(self, idx: int, value: float) -> str | None:
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
        self._write_state(patched)
        return f"global {idx} {PARAM_DEFS[idx][0]} = {value:.2f}"

    def preset_step(self, delta: int) -> str | None:
        if self.state is None:
            return None
        n = max(0, min(MAX_PRESETS - 1, active_idx(self.state) + delta))
        return self.load_preset(n)

    def toggle_bypass(self) -> str | None:
        if self.state is None:
            return None
        sd = bytearray(self.state)
        sd[-12] = 1 - sd[-12]
        sd[-7] = 1
        self._write_state(bytes(sd))
        return "bypass toggle"

    def set_slot(self, slot: int, n: int) -> str | None:
        """Load preset n into slot A/B/C (0/1/2); switches sound if slot is active."""
        if self.state is None:
            return None
        p = set_slot_patch(self.state, slot, n)
        if p is None:
            return f"slot {'ABC'[slot]} already preset {n}"
        self._write_state(p)
        if slot == self.state[-11] and self.on_preset_change:
            self.on_preset_change(n)
        return f"slot {'ABC'[slot]} -> {n}"

    def toggle_ab(self) -> str | None:
        """A/B footswitch mirror: flip the active slot between A and B."""
        if self.state is None:
            return None
        p = toggle_slot_patch(self.state)
        self._write_state(p)
        n = active_idx(self.state)
        if self.on_preset_change:
            self.on_preset_change(n)
        return f"A/B toggle -> preset {n} (slot {'ABC'[self.state[-11]]})"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
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


def open_midi_input(args, mido) -> tuple[object, str]:
    """Create the virtual 'ToneX Bridge' input (no IAC needed) or match a hint."""
    if not args.midi_in:
        try:
            port = mido.open_input(VIRTUAL_PORT_NAME, virtual=True)
            return port, f"{VIRTUAL_PORT_NAME}  (virtual — use it as Live MIDI output)"
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] virtual port unavailable ({e}); falling back to IAC")
    name = find_midi_in(args.midi_in)
    if not name:
        raise SystemExit("No MIDI input found. Enable IAC Driver in Audio MIDI Setup "
                         "or fix --midi-in. Ports: " + ", ".join(mido.get_input_names()))
    return mido.open_input(name, callback=None), name


def pname(ctx, n: int) -> str:
    nm = ctx.names[n] if ctx.names and ctx.names[n] else None
    return f"  [{nm}]" if nm else ""


def handle_midi_msg(msg, ctx) -> str | None:
    """Translate one MIDI message; returns a log line or None."""
    dev = ctx.dev
    if ctx.channel and getattr(msg, "channel", None) is not None:
        if msg.channel + 1 != ctx.channel:
            return None

    if msg.type in ("clock", "timing_clock"):   # F8; mido type is 'clock'
        if not ctx.clock_on:
            return None
        now = time.monotonic()
        ctx.last_clock = now
        b = ctx.clock.tick(now)
        if b is None:
            return None
        dev.set_param(110, b)
        return f"clock  -> bpm {b:.0f}"
    if msg.type in ("start", "continue", "stop"):
        ctx.clock.reset()
        ctx.last_clock = 0.0
        return None

    if msg.type == "program_change":
        n = msg.program
        if not (0 <= n < MAX_PRESETS):
            return f"PC {n} out of range"
        r = dev.load_preset(n)
        return f"{r}{pname(ctx, n)}" if r else None

    if msg.type == "note_on" and msg.velocity > 0:
        n = note_preset(msg.note, ctx.note_base)
        if n is None:
            return None
        r = dev.load_preset(n)
        return f"note {msg.note} -> {r}{pname(ctx, n)}" if r else None

    if msg.type != "control_change":
        return None
    c, v = msg.control, msg.value
    if c == CC_SELECT:
        if v >= MAX_PRESETS:
            return f"CC127 {v} out of range"
        r = dev.load_preset(v)
        return f"{r}{pname(ctx, v)}" if r else None
    if c == ctx.tap_cc and ctx.tap_cc and v >= 1:
        b = ctx.tap.tap(time.monotonic())
        if b is None:
            return None
        r = dev.set_param(110, b)
        return f"tap -> {r}" if r else None
    if c == CC_SLOT_A and v < MAX_PRESETS:
        return dev.set_slot(0, v)
    if c == CC_SLOT_B and v < MAX_PRESETS:
        return dev.set_slot(1, v)
    if c == CC_AB and v >= 64:
        return dev.toggle_ab()
    if c == CC_PRESET_DOWN and v >= 64:
        return dev.preset_step(-1)
    if c == CC_PRESET_UP and v >= 64:
        return dev.preset_step(+1)
    if c == CC_BYPASS and v >= 64:
        return dev.toggle_bypass()
    if c == CC_GLOBAL_VOL:
        return dev.set_param(116, -40.0 + 43.0 * (v / 127.0))
    if c == CC_BPM:
        return dev.set_param(110, 40.0 + 200.0 * (v / 127.0))
    if ctx.song_ccs:
        if c == ctx.song_ccs.get("next") and v >= 64:
            return song_nav(ctx, "next")
        if c == ctx.song_ccs.get("prev") and v >= 64:
            return song_nav(ctx, "prev")
    if c in ctx.param_cc:
        idx, _ = ctx.param_cc[c]
        return dev.set_param(idx, cc_to_value(idx, v))
    return None


def song_nav(ctx, direction: str) -> str | None:
    if not ctx.setlist:
        return "no setlist loaded"
    r = ctx.setlist.next() if direction == "next" else ctx.setlist.prev()
    if not r:
        return "setlist empty"
    song, preset = r
    ctx.dev.load_preset(preset)
    fb = getattr(ctx, "fb", None)
    if fb is not None:
        try:
            fb.send(__import__("mido").Message("control_change", control=84, value=ctx.setlist.idx))
        except Exception:  # noqa: BLE001
            pass
    return f"song -> [{ctx.setlist.idx}] {song} (preset {preset})"


def handle_osc(path: str, args: list, ctx) -> bytes | None:
    """OSC endpoint: /preset /param /vol /db /bpm /bypass /up /down /slot /toggle
    /tap /song /setlist /clock /names /status. Actions log via ctx.log;
    /names and /status reply with an OSC-encoded string.

    OSC wire format: big-endian, ints/floats/strings (tonex_osc.encode).
    """
    p = path.strip("/").lower()
    dev = ctx.dev
    line = None
    reply = None
    try:
        if p == "preset" and args:
            n = int(args[0])
            r = dev.load_preset(n)
            line = f"{r}{pname(ctx, n)}" if r else None
        elif p == "param" and len(args) >= 2:
            line = dev.set_param(int(args[0]), float(args[1]))
        elif p == "vol" and args:
            line = dev.set_param(116, -40.0 + 43.0 * max(0.0, min(1.0, float(args[0]))))
        elif p == "db" and args:
            line = dev.set_param(116, float(args[0]))
        elif p == "bpm" and args:
            line = dev.set_param(110, float(args[0]))
        elif p == "bypass":
            line = dev.toggle_bypass()
        elif p in ("up", "down"):
            line = dev.preset_step(1 if p == "up" else -1)
        elif p == "slot" and len(args) >= 2:
            line = dev.set_slot(int(args[0]), int(args[1]))
        elif p == "toggle":
            line = dev.toggle_ab()
        elif p == "tap":
            b = ctx.tap.tap(time.monotonic())
            if b is not None:
                line = dev.set_param(110, b)
        elif p == "song" and args:
            act = str(args[0]).lower()
            if act == "next":
                line = song_nav(ctx, "next")
            elif act == "prev":
                line = song_nav(ctx, "prev")
            elif act == "goto" and len(args) >= 2 and ctx.setlist:
                r = ctx.setlist.goto(int(args[1]))
                if r:
                    song, preset = r
                    ctx.dev.load_preset(preset)
                    line = f"song -> [{ctx.setlist.idx}] {song} (preset {preset})"
        elif p == "setlist" and args:
            ctx.setlist = Setlist.load(str(args[0]))
            line = f"setlist loaded: {len(ctx.setlist.entries)} songs"
        elif p == "clock" and args:
            ctx.clock_on = bool(int(args[0]))
            line = f"clock sync: {'on' if ctx.clock_on else 'off'}"
        elif p == "names":
            names = ctx.names or []
            joined = " | ".join(names[i] or "(no name)" for i in range(MAX_PRESETS))
            reply = osc_encode("/names", [joined])
        elif p == "status":
            if dev.state is None:
                reply = osc_encode("/status", ["no pedal state"])
            else:
                inf = state_info(dev.state)
                a = inf["active"]
                reply = osc_encode("/status", [
                    f"preset {a} (slot {'ABC'[inf['current_slot']]}){pname(ctx, a)}"
                    f"  bpm {inf['bpm']:.1f}  bypass {inf['bypass']}"])
        else:
            line = f"osc unknown: {path}"
    except Exception as e:  # noqa: BLE001
        line = f"osc error: {e}"
    if line:
        ctx.log(line)
    return reply


def run_command(line: str, ctx) -> list[str]:
    """Interactive CLI. Returns list of output lines (may set ctx.done)."""
    try:
        t = shlex.split(line)
    except ValueError as e:
        return [f"error: {e}"]
    if not t:
        return []
    op = t[0].lower()
    out: list[str] = []
    dev = ctx.dev

    if op in ("quit", "exit", "q"):
        ctx.done = True
        return ["bye"]
    if op in ("help", "?"):
        return [HELP]
    if op in ("preset", "p"):
        n = int(t[1])
        r = dev.load_preset(n)
        if r:
            out.append(f"{r}{pname(ctx, n)}")
    elif op == "up":
        out.append(str(dev.preset_step(+1)))
    elif op == "down":
        out.append(str(dev.preset_step(-1)))
    elif op == "bypass":
        out.append(str(dev.toggle_bypass()))
    elif op == "vol":
        v = float(t[1])
        out.append(str(dev.set_param(116, -40.0 + 43.0 * max(0.0, min(1.0, v)))))
    elif op == "db":
        out.append(str(dev.set_param(116, float(t[1]))))
    elif op == "param":
        out.append(str(dev.set_param(int(t[1]), float(t[2]))))
    elif op == "bpm":
        out.append(str(dev.set_param(110, float(t[1]))))
    elif op == "tap":
        b = ctx.tap.tap(time.monotonic())
        if b is not None:
            out.append(str(dev.set_param(110, b)) + f"  (tap {b:.0f} BPM)")
        else:
            out.append("tap: need another tap")
    elif op == "slot":
        slot = {"a": 0, "b": 1, "c": 2}.get(t[1].lower())
        if slot is None or len(t) < 3:
            out.append("usage: slot a|b|c <0-19>")
        else:
            out.append(str(dev.set_slot(slot, int(t[2]))))
    elif op == "toggle":
        out.append(str(dev.toggle_ab()))
    elif op == "state":
        if dev.state is None:
            out.append("no pedal state")
        else:
            inf = state_info(dev.state)
            out.append(f"state: A={inf['slot_a']} B={inf['slot_b']} C={inf['slot_c']} "
                       f"cur={'ABC'[inf['current_slot']]} active={inf['active']} "
                       f"bypass={inf['bypass']} bpm={inf['bpm']}")
    elif op == "osc":
        if getattr(ctx, "osc_port", None):
            out.append(f"osc endpoint: udp://{ctx.osc_host}:{ctx.osc_port}  "
                       f"(/preset /param /slot /toggle /tap /names /status)")
        else:
            out.append("osc disabled")
    elif op == "names":
        if not ctx.names:
            out.append("no names (pedal names unavailable)")
        else:
            out += [f"{i:2d}  {ctx.names[i] or '(no name)'}" for i in range(MAX_PRESETS)]
    elif op == "status":
        if dev.state is None:
            out.append("no pedal state")
        else:
            info = state_info(dev.state)
            a = info["active"]
            out.append(f"preset {a} (slot {'ABC'[info['current_slot']]}){pname(ctx, a)}"
                       f"  bpm {info['bpm']:.1f}  bypass {info['bypass']}")
        out.append(f"clock sync: {'on' if ctx.clock_on else 'off'}"
                   f"  notes: {'on (base ' + str(ctx.note_base) + ')' if ctx.note_base is not None else 'off'}"
                   f"  setlist: {len(ctx.setlist.entries) if ctx.setlist else 0} songs"
                   f"  names: {sum(1 for n in (ctx.names or []) if n)}/20")
    elif op == "song":
        if len(t) < 2:
            out.append("usage: song next|prev|goto <i>")
        elif t[1] == "next":
            out.append(str(song_nav(ctx, "next")))
        elif t[1] == "prev":
            out.append(str(song_nav(ctx, "prev")))
        elif t[1] == "goto":
            if ctx.setlist:
                r = ctx.setlist.goto(int(t[2]))
                if r:
                    song, preset = r
                    ctx.dev.load_preset(preset)
                    out.append(f"song -> [{ctx.setlist.idx}] {song} (preset {preset})")
    elif op == "setlist":
        ctx.setlist = Setlist.load(t[1])
        out.append(f"setlist loaded: {len(ctx.setlist.entries)} songs")
    elif op == "map":
        cc, idx = int(t[1]), int(t[2])
        if idx in PARAM_DEFS:
            ctx.param_cc[cc] = (idx, PARAM_DEFS[idx][0])
            out.append(f"mapped CC {cc} -> param {idx} {PARAM_DEFS[idx][0]}")
        else:
            out.append(f"param index {idx} not in PARAM_DEFS")
    elif op == "clock":
        ctx.clock_on = t[1].lower() in ("on", "1", "true")
        out.append(f"clock sync: {'on' if ctx.clock_on else 'off'}")
    else:
        out.append(f"unknown command: {op}  (help)")
    return [o for o in out if o]


def feedback_preset(ctx, n: int) -> None:
    """Emit the current preset on the virtual feedback output (if enabled)."""
    fb = getattr(ctx, "fb", None)
    if fb is None:
        return
    try:
        import mido as _m
        fb.send(_m.Message("control_change", control=CC_SELECT, value=n))
        fb.send(_m.Message("program_change", program=n))
    except Exception:  # noqa: BLE001
        pass


def reconnect(dev: TonexDevice, serial_arg: str | None) -> bool:
    dev.close()
    port = serial_arg or find_tonex_port()
    if not port:
        return False
    dev.port = port
    dev.state = None
    dev.open()
    ok = dev.wait_for_state(timeout=6.0)
    if ok:
        dev.fetch_names()
    return ok


def load_param_map(args, cfg: dict) -> dict[int, tuple[int, str]]:
    pm = dict(DEFAULT_PARAM_CC)
    for src in (cfg.get("param_map"), args.param_map_json):
        if not src:
            continue
        data = src if isinstance(src, dict) else json.load(open(src))
        for k, v in data.items():
            idx = int(v)
            pm[int(k)] = (idx, PARAM_DEFS[idx][0])
    return pm


def main() -> int:
    ap = argparse.ArgumentParser(description="TONEX One MIDI bridge (Ableton → USB serial)")
    ap.add_argument("--config", help="JSON config file (CLI flags win)")
    ap.add_argument("--serial", help="serial device (default: auto-detect VID 1963 PID 00D1)")
    ap.add_argument("--midi-in", help="MIDI input name substring (default: virtual ToneX Bridge)")
    ap.add_argument("--channel", type=int, default=None, help="MIDI channel 1-16")
    ap.add_argument("--param-map", dest="param_map_json", help="JSON {\"CC\": param_idx}")
    ap.add_argument("--note-base", type=int, default=None,
                    help="enable note->preset mapping (note N..N+19 = presets 0..19)")
    ap.add_argument("--clock-sync", dest="clock_sync", default=None, action="store_true",
                    help="MIDI clock -> pedal BPM (default, unless --no-clock)")
    ap.add_argument("--no-clock", dest="clock_sync", action="store_false", help="disable clock sync")
    ap.add_argument("--osc-port", type=int, default=9000, help="OSC UDP port (0/-no-osc disables)")
    ap.add_argument("--no-osc", dest="osc", action="store_false", default=True, help="disable OSC server")
    ap.add_argument("--osc-host", default="0.0.0.0", help="OSC bind host (0.0.0.0 for LAN/touchOSC)")
    ap.add_argument("--tap-cc", type=int, default=CC_TAP, help="tap tempo CC (0 disables)")
    ap.add_argument("--setlist", help="JSON setlist: [{\"song\":..., \"preset\":...}]")
    ap.add_argument("--song-next-cc", type=int, default=84)
    ap.add_argument("--song-prev-cc", type=int, default=85)
    ap.add_argument("--scan", action="store_true", help="list serial + MIDI ports and exit")
    ap.add_argument("--list-presets", action="store_true", help="print preset names and exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg: dict = {}
    if args.config:
        with open(args.config) as f:
            cfg = json.load(f)

    # config merge (CLI wins)
    args.serial = args.serial or cfg.get("serial")
    args.midi_in = args.midi_in or cfg.get("midi_in")
    if args.channel is None and cfg.get("channel"):
        args.channel = int(cfg["channel"])
    if args.note_base is None and cfg.get("note_base") is not None:
        args.note_base = int(cfg["note_base"])
    if args.clock_sync is None:
        args.clock_sync = bool(cfg.get("clock_sync", True))
    elif not args.clock_sync and cfg.get("clock_sync"):
        args.clock_sync = True
    if not args.setlist and cfg.get("setlist"):
        args.setlist = cfg["setlist"]
    if args.song_next_cc == 84 and cfg.get("song_next_cc"):
        args.song_next_cc = int(cfg["song_next_cc"])
    if args.song_prev_cc == 85 and cfg.get("song_prev_cc"):
        args.song_prev_cc = int(cfg["song_prev_cc"])

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
            print("  " + ("\n  ".join(mido.get_input_names()) if mido.get_input_names() else "(none)"))
            print("  (virtual " + VIRTUAL_PORT_NAME + " created automatically at runtime)")
        except Exception as e:  # noqa: BLE001
            print(f"  mido error: {e}")
        return 0

    try:
        import mido
    except ImportError:
        sys.stderr.write("mido/python-rtmidi missing — run: .venv/bin/pip install mido python-rtmidi\n")
        return 1

    param_cc = load_param_map(args, cfg)

    port = args.serial or find_tonex_port()
    if not port:
        sys.stderr.write("TONEX One not found on USB. Plug it in and re-run (or use --scan).\n")
        return 1

    dev = TonexDevice(port, verbose=args.verbose)
    osc = None
    try:
        dev.open()
        print(f"serial  : {port}")
        if not dev.wait_for_state():
            sys.stderr.write("No state response from pedal. Is it powered and in pedal mode?\n")
            return 1
        names = dev.fetch_names()
        info = state_info(dev.state)
        print(f"ready   : preset {info['active']} (slot {'ABC'[info['current_slot']]})"
              f"{pname(SimpleNamespace(names=names), info['active'])}"
              f"  bpm {info['bpm']:.0f}  bypass {info['bypass']}  "
              f"names {sum(1 for n in names if n)}/20")
        if args.list_presets:
            for i in range(MAX_PRESETS):
                print(f"  {i:2d}  {names[i] or '(no name)'}")
            return 0

        midi_port, midi_label = open_midi_input(args, mido)
        print(f"midi in : {midi_label}")
        if args.channel:
            print(f"channel : {args.channel}")

        setlist = Setlist.load(args.setlist) if args.setlist else None
        song_ccs = {"next": args.song_next_cc, "prev": args.song_prev_cc} if setlist else {}

        def log(line: str) -> None:
            print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)

        # virtual MIDI feedback output (Live/Max see the current preset)
        fb = None
        try:
            fb = mido.open_output(VIRTUAL_PORT_NAME + " Out", virtual=True)
            print(f"feedback: {VIRTUAL_PORT_NAME} Out (virtual MIDI output)")
        except Exception:  # noqa: BLE001
            pass

        ctx = SimpleNamespace(
            dev=dev, param_cc=param_cc, names=names, note_base=args.note_base,
            channel=args.channel, clock_on=bool(args.clock_sync),
            clock=ClockSync(), last_clock=0.0, done=False,
            setlist=setlist, song_ccs=song_ccs,
            tap=TapTempo(), tap_cc=args.tap_cc, fb=fb, log=log,
            osc_host=args.osc_host, osc_port=None,
        )
        dev.on_preset_change = lambda n: feedback_preset(ctx, n)

        if args.osc and args.osc_port > 0:
            def osc_handler(path, argv, addr):
                return handle_osc(path, argv, ctx)
            osc = OscServer(args.osc_port, osc_handler, host=args.osc_host)
            osc.start()
            ctx.osc_port = osc.bound_port
            print(f"osc      : udp://{args.osc_host}:{ctx.osc_port}  "
                  f"(/preset /param /slot /toggle /tap /names /status)")
        else:
            osc = None
            print("osc      : disabled")

        print(f"clock    : {'on (MIDI clock -> BPM)' if ctx.clock_on else 'off'}"
              f"   tap: {('CC ' + str(args.tap_cc)) if args.tap_cc else 'off'}"
              f"   notes: {('on (base ' + str(args.note_base) + ')') if args.note_base is not None else 'off'}"
              f"   setlist: {len(setlist.entries) if setlist else 0} songs"
              + (f" (CC {args.song_next_cc}/{args.song_prev_cc} song next/prev)" if setlist else ""))
        print("--- bindings ---")
        print("  PC 0-19 | CC 127 0-19   -> load preset")
        print(f"  CC {CC_PRESET_DOWN}/{CC_PRESET_UP}             -> preset down/up")
        print(f"  CC {CC_TAP}                  -> tap tempo")
        print(f"  CC {CC_SLOT_A}/{CC_SLOT_B}/{CC_AB}               -> slot A/B load, A/B toggle")
        print(f"  CC {CC_BYPASS}                -> bypass toggle")
        print(f"  CC {CC_GLOBAL_VOL}               -> global volume")
        print(f"  CC {CC_BPM}                -> BPM")
        for cc, (idx, name) in sorted(param_cc.items()):
            print(f"  CC {cc:<3}                -> {name} (param {idx})")
        print("  type 'help' for the interactive CLI")
        print("---")

        cmd_q: queue.Queue = queue.Queue()

        def on_msg(m):
            cmd_q.put(m)

        midi_port.callback = on_msg  # type: ignore[attr-defined]
        with midi_port:
            while not ctx.done:
                # interactive stdin
                if select.select([sys.stdin], [], [], 0)[0]:
                    line = sys.stdin.readline()
                    if not line:                      # EOF (piped)
                        if sys.stdin.isatty():         # real terminal: quit
                            ctx.done = True
                            break
                        continue                      # headless: ignore EOF
                    for o in run_command(line, ctx):
                        log(o)
                # MIDI
                msg = None
                try:
                    msg = cmd_q.get(timeout=0.1)
                except queue.Empty:
                    pass
                if msg is not None:
                    out = handle_midi_msg(msg, ctx)
                    if out:
                        log(out)
                # clock idle (transport stopped) and reconnect
                if ctx.clock_on and ctx.last_clock and time.monotonic() - ctx.last_clock > 1.5:
                    ctx.clock.reset()
                    ctx.last_clock = 0.0
                if dev.failed:
                    log("serial link lost — retrying...")
                    while dev.failed:
                        time.sleep(1.5)
                        if reconnect(dev, args.serial):
                            break
                    if dev.state is not None:
                        names = dev.fetch_names()
                    ctx.names = names
                    info = state_info(dev.state)
                    log(f"pedal reconnected: preset {info['active']} (slot {'ABC'[info['current_slot']]})"
                        f"{pname(ctx, info['active'])}")
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        dev.close()
        if osc is not None:
            osc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())