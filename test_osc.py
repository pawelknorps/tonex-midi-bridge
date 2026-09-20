"""test_osc.py — OSC encode/decode vectors + UDP server round-trip (stdlib only).

Run: .venv/bin/python -m pytest test_osc.py -q  (or: python test_osc.py)
"""

import socket

import pytest

from tonex_osc import OscServer, decode, encode


def test_encode_known_vector():
    # /preset ,i 5  ->  path 8B, tags 4B, int32 BE
    assert encode("/preset", [5]) == bytes([
        0x2F, 0x70, 0x72, 0x65, 0x73, 0x65, 0x74, 0x00,   # "/preset\0"
        0x2C, 0x69, 0x00, 0x00,                           # ",i\0\0"
        0x00, 0x00, 0x00, 0x05,                           # 5
    ])


def test_decode_vector():
    path, args = decode(encode("/preset", [5]))
    assert path == "/preset" and args == [5]


@pytest.mark.parametrize("path,args", [
    ("/preset", [5]),
    ("/bpm", [120.0]),
    ("/setlist", ["my file.json"]),
    ("/mixed", [1, 2.5, "abc"]),
    ("/a", []),
    ("/x", [True, False]),
])
def test_roundtrip(path, args):
    assert decode(encode(path, args)) == (path, args)


def test_alignment_edge_cases():
    # 3-char path + 1-char tag must still pad to 4
    b = encode("/ab", [1])
    assert len(b) == 12 and b[3] == 0
    # string arg exactly one 4-byte word
    path, args = decode(encode("/s", ["abcd"]))
    assert args == ["abcd"]


def test_decode_garbage():
    with pytest.raises(ValueError):
        decode(b"\x00\x00\x00\x00")
    with pytest.raises(ValueError):
        decode(b"/x\x00\x00,z\x00\x00\x00\x00\x00\x00")   # unsupported tag 'z'


def test_server_roundtrip():
    received = {}

    def handler(path, args, addr):
        received["path"] = path
        received["args"] = args
        return encode("/reply", ["ok " + str(len(args))])

    srv = OscServer(0, handler, host="127.0.0.1")
    srv.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3.0)
        s.sendto(encode("/ping", [1, 2]), ("127.0.0.1", srv.bound_port))
        data, _ = s.recvfrom(65536)
        s.close()
    finally:
        srv.stop()
    assert decode(data) == ("/reply", ["ok 2"])
    assert received == {"path": "/ping", "args": [1, 2]}


def test_server_survives_bad_message():
    """Garbage datagram must not kill the server thread."""
    srv = OscServer(0, lambda *a: None, host="127.0.0.1")
    srv.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"\xff\xff garbage", ("127.0.0.1", srv.bound_port))
        s.close()
        srv.thread.join(1.0)
        assert srv.thread.is_alive(), "server thread died on garbage"
    finally:
        srv.stop()


if __name__ == "__main__":
    import sys
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