"""test_features.py — pure-logic tests for the SOTA features (no hardware).

Covers: preset-name parsing, MIDI-clock -> BPM sync (hysteresis), setlist
navigation, note->preset mapping, message router, interactive CLI commands.
Run: .venv/bin/python -m pytest test_features.py -q  (or: python test_features.py)
"""

import time
from types import SimpleNamespace

from tonex_features import ClockSync, Setlist, TapTempo, note_preset
from tonex_proto import (
    MAX_PRESETS, PRESET_NAME_MARKER, parse_preset_name,
    set_slot_patch, toggle_slot_patch,
)
from test_proto import STATE


def _name_payload(name: str) -> bytes:
    return bytes([0xB9, 0x03, 0x81, 0x00, 0x03]) + PRESET_NAME_MARKER + \
        name.encode() + bytes(32 - len(name))


def test_parse_preset_name():
    assert parse_preset_name(_name_payload("Jazz Lead")) == "Jazz Lead"
    assert parse_preset_name(_name_payload("Clean Cartman")) == "Clean Cartman"
    assert parse_preset_name(b"\x00\x01\x02") is None
    empty = bytes([0xB9, 0x03]) + PRESET_NAME_MARKER + bytes(32)
    assert parse_preset_name(empty) is None


def _clock_train(bpm: float, n: int, start: float = 1000.0) -> list[float]:
    dt = 60.0 / (bpm * 24.0)
    return [start + i * dt for i in range(n)]


def test_clock_sync_commits_only_on_change():
    cs = ClockSync()
    train = _clock_train(120.0, 30)
    writes = [cs.tick(t) for t in train]
    commits = [w for w in writes if w is not None]
    assert commits == [120.0], f"expected single 120 commit, got {commits}"

    # tempo change to 100 -> one commit
    train2 = _clock_train(100.0, 30, start=train[-1] + 0.0166)
    commits2 = [cs.tick(t) for t in train2]
    assert 100.0 in commits2, "tempo change not committed"


def test_clock_sync_ignores_jitter():
    cs = ClockSync()
    t = 1000.0
    for _ in range(40):                      # alternating 131.6 / 108.7 bpm
        cs.tick(t)
        t += 0.019
        cs.tick(t)
        t += 0.023
    assert cs.last_written is None, "jittery stream must never commit"


def test_clock_sync_reset_and_gate():
    cs = ClockSync()
    cs.tick(1000.0)
    cs.reset()
    assert cs.samples == [] and cs.last_t is None
    # out-of-range bpm clears the window instead of committing garbage
    cs.tick(1000.0)
    for i in range(20):
        cs.tick(1000.0 + i * 0.05)           # bpm ≈ 50
    assert cs.last_written == 50


def test_setlist_navigation():
    sl = Setlist([{"song": "Intro", "preset": 0},
                  {"song": "Verse", "preset": 5},
                  {"song": "Chorus", "preset": 9}])
    assert sl.goto(0) == ("Intro", 0)
    assert sl.next() == ("Verse", 5)
    assert sl.next() == ("Chorus", 9)
    assert sl.next() == ("Intro", 0)         # wraps
    assert sl.prev() == ("Chorus", 9)
    assert Setlist([]).next() is None
    assert Setlist([]).prev() is None
    sl2 = Setlist([{"preset": 3}, "garbage"])
    assert len(sl2.entries) == 1 and sl2.entries[0]["preset"] == 3


def test_setlist_load_roundtrip(tmp_path):
    import json
    p = tmp_path / "sl.json"
    p.write_text(json.dumps([{"song": "A", "preset": 1}, {"song": "B", "preset": 2}]))
    sl = Setlist.load(str(p))
    assert len(sl.entries) == 2 and sl.next() == ("B", 2)


def test_note_mapping():
    assert note_preset(36, 36) == 0
    assert note_preset(55, 36) == 19
    assert note_preset(56, 36) is None
    assert note_preset(35, 36) is None
    assert note_preset(60, None) is None     # disabled


# ---- message router + CLI (stub device, no hardware) ----------------------
class StubDev:
    def __init__(self):
        self.state = b"\x00" * 40
        self.last = None

    def load_preset(self, n, toggle_on_repeat=False):
        self.last = ("preset", n)
        return f"preset -> {n}"

    def set_param(self, idx, v):
        self.last = ("param", idx, v)
        return f"param {idx} = {v:.2f}"

    def preset_step(self, d):
        self.last = ("step", d)
        return f"step {d}"

    def toggle_bypass(self):
        self.last = ("bypass",)
        return "bypass toggle"

    def set_slot(self, slot, n):
        self.last = ("slot", slot, n)
        return f"slot {'ABC'[slot]} -> {n}"

    def toggle_ab(self):
        self.last = ("ab",)
        return "A/B toggle"

    def snapshot_save(self, slot):
        self.last = ("snap", "save", slot)
        return f"snapshot {slot} saved (A=0 B=8 cur=A bpm=45.0)"

    def snapshot_recall(self, slot):
        self.last = ("snap", "recall", slot)
        return f"snapshot {slot} recalled (A=0 B=8 cur=A preset 0 bpm=45.0)"

    def snapshot_swap(self):
        self.last = ("snap", "swap")
        return "snapshots 1 <-> 2 swapped"

    def snapshot_list(self):
        return ["  1: A=0 B=8 C=12 cur=A bypass=0 bpm=45.0"]


def _mkctx(note_base=None, clock_on=False, setlist=None, tap_cc=10):
    import tonex_bridge as tb
    return SimpleNamespace(
        dev=StubDev(), param_cc=dict(tb.DEFAULT_PARAM_CC),
        names=[None] * MAX_PRESETS, note_base=note_base, channel=None,
        clock_on=clock_on, clock=ClockSync(), last_clock=0.0, done=False,
        setlist=setlist,
        song_ccs={"next": 84, "prev": 85} if setlist else {},
        tap=TapTempo(), tap_cc=tap_cc, fb=None, log=lambda s: None,
        osc_host="127.0.0.1", osc_port=None,
    )


def test_router_pc_cc_note_clock():
    import mido
    import tonex_bridge as tb

    ctx = _mkctx()
    assert tb.handle_midi_msg(mido.Message("program_change", program=7), ctx).startswith("preset -> 7")
    assert ctx.dev.last == ("preset", 7)

    assert tb.handle_midi_msg(mido.Message("control_change", control=127, value=12), ctx).startswith("preset -> 12")
    assert ctx.dev.last == ("preset", 12)

    assert tb.handle_midi_msg(mido.Message("control_change", control=122, value=64), ctx) is not None
    assert ctx.dev.last[0] == "param" and ctx.dev.last[1] == 116

    assert tb.handle_midi_msg(mido.Message("control_change", control=123, value=100), ctx) == "bypass toggle"
    assert ctx.dev.last == ("bypass",)

    # param map
    assert tb.handle_midi_msg(mido.Message("control_change", control=102, value=127), ctx) is not None
    assert ctx.dev.last == ("param", 20, 10.0)

    # notes off by default
    assert tb.handle_midi_msg(mido.Message("note_on", note=36, velocity=100), ctx) is None
    # notes on
    ctx2 = _mkctx(note_base=36)
    r = tb.handle_midi_msg(mido.Message("note_on", note=40, velocity=100), ctx2)
    assert r is not None and ctx2.dev.last == ("preset", 4)
    # note_off ignored
    assert tb.handle_midi_msg(mido.Message("note_on", note=40, velocity=0), ctx2) is None

    # channel filter (mido 0-based; bridge --channel is 1..16)
    ctx3 = _mkctx()
    ctx3.channel = 2
    assert tb.handle_midi_msg(mido.Message("control_change", control=127, value=5, channel=0), ctx3) is None

    # clock sync: commit logic is covered by ClockSync tests above; here just
    # verify the router wires timing_clock into the sync and transport reset
    ctx4 = _mkctx(clock_on=True)
    assert tb.handle_midi_msg(mido.Message("clock"), ctx4) is None
    assert tb.handle_midi_msg(mido.Message("stop"), ctx4) is None


def test_song_nav_and_cli():
    import tonex_bridge as tb

    sl = Setlist([{"song": "A", "preset": 1}, {"song": "B", "preset": 2}])
    ctx = _mkctx(setlist=sl)
    out = tb.song_nav(ctx, "next")
    assert out and "B" in out and ctx.dev.last[1] == 2

    # CLI commands
    ctx2 = _mkctx()
    assert "preset -> 3" in tb.run_command("preset 3", ctx2)[0]
    assert "preset -> 8" in tb.run_command("p 8", ctx2)[0]
    assert tb.run_command("param 20 5.5", ctx2)[0].startswith("param 20")
    assert ctx2.dev.last == ("param", 20, 5.5)
    tb.run_command("map 40 20", ctx2)
    assert ctx2.param_cc[40][0] == 20
    tb.run_command("clock off", ctx2)
    assert ctx2.clock_on is False
    tb.run_command("clock on", ctx2)
    assert ctx2.clock_on is True
    assert tb.run_command("bogus", ctx2)[0].startswith("unknown")
    tb.run_command("quit", ctx2)
    assert ctx2.done is True


def test_slot_patches():
    p = set_slot_patch(STATE, 0, 5)
    assert p is not None and p[-18] == 5 and p[-16] == 7 and p[-7] == 1
    assert set_slot_patch(STATE, 0, 2) is None            # already in slot A
    p = set_slot_patch(STATE, 1, 9)
    assert p is not None and p[-16] == 9
    p = set_slot_patch(STATE, 2, 12)
    assert p is not None and p[-14] == 12
    try:
        set_slot_patch(STATE, 4, 0)
        assert False, "should raise"
    except ValueError:
        pass


def test_toggle_slot_patch():
    p = toggle_slot_patch(STATE)          # currentSlot 0 -> 1
    assert p[-11] == 1 and p[-12] == 0 and p[-7] == 1
    assert toggle_slot_patch(p)[-11] == 0  # and back


def test_tap_tempo():
    tt = TapTempo()
    assert tt.tap(100.0) is None                         # single tap
    assert tt.tap(100.5) == 120.0                        # 0.5 s -> 120 BPM
    assert tt.tap(101.0) is None                         # same tempo, no rewrite
    tt2 = TapTempo()
    tt2.tap(100.0)
    tt2.tap(103.0)                                       # gap > max_gap: reset
    tt2.tap(103.5)
    assert tt2.last_written == 120.0


def test_router_slots_tap_tempo():
    import mido
    import tonex_bridge as tb

    ctx = _mkctx()
    assert tb.handle_midi_msg(mido.Message("control_change", control=124, value=5), ctx) is not None
    assert ctx.dev.last == ("slot", 0, 5)
    assert tb.handle_midi_msg(mido.Message("control_change", control=125, value=9), ctx) is not None
    assert ctx.dev.last == ("slot", 1, 9)
    assert tb.handle_midi_msg(mido.Message("control_change", control=126, value=100), ctx) is not None
    assert ctx.dev.last == ("ab",)
    # tap tempo: two CC10 taps ~0.5 s apart -> ~120 BPM write (macOS sleep
    # granularity ±10 % — assert the write happened in the expected band)
    assert tb.handle_midi_msg(mido.Message("control_change", control=10, value=100), ctx) is None
    time.sleep(0.52)
    r = tb.handle_midi_msg(mido.Message("control_change", control=10, value=100), ctx)
    assert r is not None and ctx.dev.last[0] == "param" and ctx.dev.last[1] == 110
    assert 105 <= ctx.dev.last[2] <= 125, ctx.dev.last


def test_osc_route():
    import tonex_bridge as tb
    from tonex_osc import decode

    ctx = _mkctx()
    logs = []
    ctx.log = logs.append

    tb.handle_osc("/preset", [7], ctx)
    assert ctx.dev.last == ("preset", 7) and logs and "preset -> 7" in logs[-1]
    tb.handle_osc("/param", [20, 5.5], ctx)
    assert ctx.dev.last == ("param", 20, 5.5)
    tb.handle_osc("/slot", [1, 9], ctx)
    assert ctx.dev.last == ("slot", 1, 9)
    tb.handle_osc("/toggle", [], ctx)
    assert ctx.dev.last == ("ab",)
    tb.handle_osc("/vol", [0.5], ctx)
    assert ctx.dev.last == ("param", 116, -18.5)          # -40 + 43*0.5
    tb.handle_osc("/nope", [], ctx)
    assert "unknown" in logs[-1]
    reply = tb.handle_osc("/names", [], ctx)
    assert reply is not None and decode(reply)[0] == "/names"
    reply = tb.handle_osc("/status", [], ctx)
    assert reply is not None and "preset" in decode(reply)[1][0]


def test_osc_snapshot_and_names():
    import tonex_bridge as tb
    from tonex_osc import decode

    ctx = _mkctx()
    logs = []
    ctx.log = logs.append
    ctx.names = ["Alpha One", None] + ["(no name)"] * 18

    # ename appends the preset name to slot/toggle lines
    assert tb.ename(ctx, "A/B toggle -> preset 0 (slot A)") == "A/B toggle -> preset 0 (slot A) [Alpha One]"
    assert tb.ename(ctx, "A/B toggle -> preset 1 (slot B)") == "A/B toggle -> preset 1 (slot B)"
    assert tb.ename(ctx, None) is None

    tb.handle_osc("/snapshot", ["save", 1], ctx)
    assert ctx.dev.last == ("snap", "save", 1)
    tb.handle_osc("/snapshot", ["recall", 1], ctx)
    assert ctx.dev.last == ("snap", "recall", 1)
    assert "Alpha One" in logs[-1]                        # name in recall log
    tb.handle_osc("/snapshot", ["swap"], ctx)
    assert ctx.dev.last == ("snap", "swap")
    reply = tb.handle_osc("/snapshot", ["list"], ctx)
    assert reply is None and logs[-1].startswith("snapshots:")
    # routes
    assert tb.run_command("snapshot save 2", ctx)[0].startswith("snapshot 2 saved")
    assert tb.run_command("snapshot list", ctx)[0].startswith("  1:")
    assert tb.run_command("snapshot", ctx)[0].startswith("usage:")


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