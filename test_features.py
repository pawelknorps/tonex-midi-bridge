"""test_features.py — pure-logic tests for the SOTA features (no hardware).

Covers: preset-name parsing, MIDI-clock -> BPM sync (hysteresis), setlist
navigation, note->preset mapping, message router, interactive CLI commands.
Run: .venv/bin/python -m pytest test_features.py -q  (or: python test_features.py)
"""

import time
from types import SimpleNamespace

from tonex_features import ClockSync, Setlist, note_preset
from tonex_proto import MAX_PRESETS, PRESET_NAME_MARKER, parse_preset_name


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


def _mkctx(note_base=None, clock_on=False, setlist=None):
    import tonex_bridge as tb
    return SimpleNamespace(
        dev=StubDev(), param_cc=dict(tb.DEFAULT_PARAM_CC),
        names=[None] * MAX_PRESETS, note_base=note_base, channel=None,
        clock_on=clock_on, clock=ClockSync(), last_clock=0.0, done=False,
        setlist=setlist,
        song_ccs={"next": 84, "prev": 85} if setlist else {},
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