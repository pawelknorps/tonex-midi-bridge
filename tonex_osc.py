"""tonex_osc.py — minimal OSC (Open Sound Control) transport, stdlib only.

Implements just enough of OSC 1.0 (UDP) for the bridge: messages with an
address pattern and i / f / s arguments, big-endian per spec, 4-byte
alignment with zero padding. No timetags, no bundles.

Usage:
    server = OscServer(port, handler)   # handler(path, args, addr) -> bytes|None
    server.start()                      # background thread
    server.stop()
"""

from __future__ import annotations

import socket
import struct
import threading


def _pad(b: bytes, n: int = 4) -> bytes:
    return b + b"\x00" * ((n - len(b) % n) % n)


def encode(path: str, args: list | None = None) -> bytes:
    """Encode an OSC message (path + i/f/s args). OSC strings are
    NUL-terminated AND padded to 4 bytes (spec 1.0)."""
    args = args or []
    out = bytearray(_pad(path.encode("utf-8") + b"\x00"))
    tags = "," + "".join(
        "i" if isinstance(a, bool) or isinstance(a, int)
        else "f" if isinstance(a, float)
        else "s" for a in args)
    out += _pad(tags.encode("ascii") + b"\x00")
    for a in args:
        if isinstance(a, bool):
            out += struct.pack(">i", 1 if a else 0)
        elif isinstance(a, int):
            out += struct.pack(">i", a)
        elif isinstance(a, float):
            out += struct.pack(">f", a)
        else:
            out += _pad(str(a).encode("utf-8") + b"\x00")
    return bytes(out)


def decode(data: bytes) -> tuple[str, list]:
    """Decode an OSC message -> (path, args). Raises ValueError on garbage."""
    adr_end = data.find(b"\x00")
    if adr_end <= 0:
        raise ValueError("bad address")
    path = data[:adr_end].decode("utf-8", "replace")
    ti = ((adr_end + 1) + 3) // 4 * 4
    if len(data) <= ti or data[ti:ti + 1] != b",":
        return path, []
    tt_end = data.find(b"\x00", ti)
    if tt_end < 0:
        tt_end = len(data)
    tags = data[ti:tt_end].decode("ascii", "replace")
    args: list = []
    ai = ((tt_end + 1) + 3) // 4 * 4
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", data[ai:ai + 4])[0])
            ai += 4
        elif t == "f":
            args.append(struct.unpack(">f", data[ai:ai + 4])[0])
            ai += 4
        elif t == "s":
            e = data.find(b"\x00", ai)
            if e < 0:
                e = len(data)
            args.append(data[ai:e].decode("utf-8", "replace"))
            ai = ((e + 1) + 3) // 4 * 4
        else:
            raise ValueError(f"unsupported type tag {t!r}")
    return path, args


class OscServer:
    """UDP OSC server on a background thread. Handler -> reply bytes or None."""

    def __init__(self, port: int, handler, host: str = "0.0.0.0"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(0.2)
        self.bound_port = self.sock.getsockname()[1]
        self.handler = handler
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="tonex-osc")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                path, args = decode(data)
                reply = self.handler(path, args, addr)
            except Exception:  # noqa: BLE001 — never kill the server thread
                continue
            if reply:
                try:
                    self.sock.sendto(reply, addr)
                except OSError:
                    pass