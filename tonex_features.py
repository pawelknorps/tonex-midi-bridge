"""tonex_features.py — pure bridge logic (no hardware): MIDI-clock → BPM sync,
setlist navigation, note→preset mapping. Testing: see test_features.py.
"""

from __future__ import annotations

import json


class ClockSync:
    """Turn a 24ppq MIDI clock stream into stable BPM writes to the pedal.

    - collects per-interval BPM samples (rolling window)
    - waits `settle` samples before first commit (avoids transport-start spikes)
    - gates on spread (jitter): noisy streams are ignored
    - commits only when the averaged BPM moved by >= min_change vs last write
    - cooldown between commits prevents write spam
    """

    def __init__(self, settle: int = 6, window: int = 16, min_change: float = 1.0,
                 cooldown: float = 0.5, bpm_min: float = 40.0, bpm_max: float = 240.0):
        self.settle = settle
        self.window = window
        self.min_change = min_change
        self.cooldown = cooldown
        self.bpm_min = bpm_min
        self.bpm_max = bpm_max
        self.samples: list[float] = []
        self.last_t: float | None = None
        self.last_written: float | None = None
        self.cooldown_until = 0.0

    def reset(self) -> None:
        self.samples.clear()
        self.last_t = None

    def tick(self, now: float) -> float | None:
        """Feed one clock pulse; returns the BPM to write, or None."""
        if self.last_t is None:
            self.last_t = now
            return None
        dt = now - self.last_t
        self.last_t = now
        if dt <= 0.0:
            return None
        bpm = 60.0 / (dt * 24.0)
        if not (self.bpm_min <= bpm <= self.bpm_max):
            self.samples.clear()
            return None
        self.samples.append(bpm)
        if len(self.samples) > self.window:
            del self.samples[0]
        if len(self.samples) < self.settle:
            return None
        spread = max(self.samples) - min(self.samples)
        if spread > 4.0:                       # jittery stream — don't commit
            return None
        avg = sum(self.samples) / len(self.samples)
        b = round(avg)
        if self.last_written is not None and abs(b - self.last_written) < self.min_change:
            return None
        if now < self.cooldown_until:
            return None
        self.last_written = b
        self.cooldown_until = now + self.cooldown
        return float(b)


class Setlist:
    """Ordered song → preset list with wrap-around navigation."""

    def __init__(self, entries: list[dict] | None = None):
        self.entries = entries or []
        self.idx = 0
        self.clean()

    @classmethod
    def load(cls, path: str) -> "Setlist":
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("songs", [])
        return cls(data)

    def clean(self) -> None:
        self.entries = [{"song": str(e.get("song", "?" if e else "") or "?"),
                         "preset": int(e.get("preset", 0))}
                        for e in self.entries
                        if isinstance(e, dict) and "preset" in e]
        if not self.entries:
            self.idx = 0
        else:
            self.idx = max(0, min(self.idx, len(self.entries) - 1))

    def goto(self, i: int) -> tuple[str, int] | None:
        if not self.entries:
            return None
        self.idx = i % len(self.entries)
        e = self.entries[self.idx]
        return e["song"], e["preset"]

    def next(self) -> tuple[str, int] | None:
        return self.goto(self.idx + 1 if self.entries else 0)

    def prev(self) -> tuple[str, int] | None:
        if not self.entries:
            return None
        self.idx = (self.idx - 1) % len(self.entries)
        e = self.entries[self.idx]
        return e["song"], e["preset"]


def note_preset(note: int, base: int | None) -> int | None:
    """Map a MIDI note to a preset index 0..19 when note mapping is enabled."""
    if base is None:
        return None
    i = note - base
    return i if 0 <= i < 20 else None