"""test_device.py — TonexDevice over a fake echoing serial link.

The fake serial answers every set_state write with a pedal-style state echo
(header with N=payload[6], state = last N bytes), so _write_state's pacing
resolves immediately — no 1 s timeouts, deterministic and fast.

Run: .venv/bin/python -m pytest test_device.py -q  (or: python test_device.py)
"""

import os
import sys
import threading
import time

from tonex_proto import frame, unframe

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def mk_state(slot_a=0, slot_b=8, slot_c=12, cur=0, bypass=0) -> bytes:
    st = bytearray(164)
    st[-18], st[-16], st[-14] = slot_a, slot_b, slot_c
    st[-11] = cur
    st[-12] = bypass
    st[-7] = 1                                    # DMON
    return bytes(st)


def extract_state(data: bytes) -> bytes | None:
    """set_state request layout: ... 0x82 <lenLE:2> 0x80 0x0B 0x03 <state>"""
    payload = unframe(data)
    for i in range(len(payload) - 7):
        if payload[i] == 0x82 and payload[i + 3] == 0x80 \
                and payload[i + 4] == 0x0B and payload[i + 5] == 0x03:
            n = payload[i + 1] | (payload[i + 2] << 8)
            return bytes(payload[i + 6:i + 6 + n])
    return None


class EchoSerial:
    """Stores writes; echoes a parse_state-valid state frame per set_state."""

    def __init__(self):
        self.writes: list[bytes] = []
        self._cv = threading.Condition()
        self._readable: list[bytes] = []

    def write(self, data: bytes) -> None:
        with self._cv:
            self.writes.append(bytes(data))
            sd = extract_state(data)
            if sd is not None:
                header = (bytes([0xB9, 0x03, 0x81, 0x06, 0x03, 0x82, len(sd)])
                          + bytes([0x80, 0x0B, 0x03])
                          + bytes([0xB9, 0x02, 0x81, 0x06, 0x03, 0x0B]))
                self._readable.append(frame(header + sd))
                self._cv.notify_all()

    def read(self, n: int) -> bytes:
        with self._cv:
            return self._readable.pop(0) if self._readable else b""

    def close(self):
        with self._cv:
            self._readable.clear()
            self._cv.notify_all()


def make_dev(state: bytes):
    import tonex_bridge as tb

    fake = EchoSerial()
    real = tb.serial.Serial
    tb.serial.Serial = lambda *a, **k: fake
    try:
        dev = tb.TonexDevice("FAKE")
        dev.open()
        dev.state = state
        time.sleep(0.05)                      # reader thread spins up
        return dev, fake
    finally:
        tb.serial.Serial = real


def test_load_preset_writes_patch():
    dev, fake = make_dev(mk_state(slot_a=0, cur=0))
    try:
        assert dev.load_preset(5) == "preset -> 5"
        time.sleep(0.1)
        sd = extract_state(fake.writes[-1])
        assert sd is not None and sd[-18] == 5
        assert sd[-7] == 1 and sd[-12] == 0
        time.sleep(0.2)
        assert dev.state is not None and dev.state[-18] == 5   # echo adopted
    finally:
        dev.close()


def test_snapshot_roundtrip():
    dev, fake = make_dev(mk_state(slot_a=0, cur=0))
    try:
        original = mk_state(slot_a=0, cur=0)
        assert "snapshot 1 saved" in dev.snapshot_save(1)
        n0 = len(fake.writes)
        r = dev.snapshot_recall(1)
        assert r is not None and "preset 0" in r
        assert len(fake.writes) == n0 + 1               # recall wrote once
        assert extract_state(fake.writes[-1]) == original
    finally:
        dev.close()


def test_snapshot_undo():
    dev, fake = make_dev(mk_state(slot_a=0, cur=0))
    try:
        dev.snapshot_save(1)                          # original
        dev.load_preset(7)                            # mess it up (slotA=7)
        time.sleep(0.15)
        assert dev.state[-18] == 7
        r = dev.snapshot_recall(1)                    # exact undo
        time.sleep(0.2)
        assert r is not None and "preset 0" in r
        assert extract_state(fake.writes[-1]) == mk_state(slot_a=0, cur=0)
        assert dev.state == mk_state(slot_a=0, cur=0)  # echo-verified
    finally:
        dev.close()


def test_snapshot_error_paths():
    dev, fake = make_dev(mk_state())
    try:
        n = len(fake.writes)
        assert "missing" in dev.snapshot_recall(9)
        assert "out of range" in dev.snapshot_save(11)
        assert len(fake.writes) == n                  # nothing written
    finally:
        dev.close()


def test_snapshot_swap():
    dev, _ = make_dev(mk_state(slot_a=0))
    try:
        s1 = mk_state(slot_a=0)
        s2 = mk_state(slot_a=7)
        dev.snapshots[1] = s1
        dev.snapshots[2] = s2
        assert "swapped" in dev.snapshot_swap()
        assert dev.snapshots[1] == s2 and dev.snapshots[2] == s1
        assert dev.snapshot_list()                      # both listed
    finally:
        dev.close()


def test_burst_writes_no_stale_state():
    dev, fake = make_dev(mk_state(slot_a=0, slot_b=8, cur=0))
    try:
        dev.load_preset(5)
        dev.set_slot(0, 7)
        dev.toggle_ab()
        dev.toggle_ab()
        time.sleep(0.3)
        states = [extract_state(w) for w in fake.writes]
        assert len(states) == 4
        assert states[0][-18] == 5                     # load preset
        assert states[1][-18] == 7                     # saw write1
        assert states[1][-16] == 8                     # slotB untouched
        assert states[2][-11] == 1                     # cur A -> B
        assert states[3][-11] == 0                     # and back (saw write3)
        assert all(s[-16] == 8 and s[-14] == 12 for s in states)
        assert all(s[-12] == 0 and s[-7] == 1 for s in states)
        assert dev.state[-18] == 7 and dev.state[-11] == 0
    finally:
        dev.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)