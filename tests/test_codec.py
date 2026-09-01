"""Codec tests. Phase 1.

The cases here are the ones that break naive socket code in practice: a frame
split across two reads, two frames arriving in one read, a multi-byte
character cut in half, and non-ASCII text surviving the round trip.
"""

from __future__ import annotations

import pytest

from im.common.codec import LineBuffer, decode, encode
from im.common.frames import Frame, MessageType, ProtocolError

CHINESE = "你好，世界"
EMOJI = "🔐 ciphertext 🛰"


def test_frame_round_trips() -> None:
    original = Frame(type=MessageType.MSG, sender="alice", to="#general", body="hello")
    restored = decode(encode(original).decode("utf-8").rstrip("\n"))

    assert restored.type is MessageType.MSG
    assert restored.sender == "alice"
    assert restored.to == "#general"
    assert restored.body == "hello"
    assert restored.id == original.id
    assert restored.ts == original.ts


def test_from_is_the_wire_name_for_sender() -> None:
    """`from` is a Python keyword, so the attribute is `sender`. The wire is
    unaffected -- and the client team is reading docs/protocol.md, not this."""
    wire = Frame(type=MessageType.MSG, sender="alice").to_dict()
    assert wire["from"] == "alice"
    assert "sender" not in wire


def test_none_fields_are_omitted_from_the_wire() -> None:
    wire = Frame(type=MessageType.PING).to_dict()
    assert set(wire) == {"v", "id", "type", "ts"}


def test_type_specific_payload_is_flattened() -> None:
    frame = Frame(type=MessageType.LOGIN, data={"user": "alice", "pass_hash": "abc"})
    restored = decode(encode(frame).decode("utf-8"))
    assert restored.data == {"user": "alice", "pass_hash": "abc"}


def test_payload_may_not_shadow_a_reserved_field() -> None:
    with pytest.raises(ProtocolError, match="reserved"):
        encode(Frame(type=MessageType.MSG, data={"body": "smuggled"}))


# --------------------------------------------------------------- framing ---


def test_one_recv_can_carry_two_frames() -> None:
    """TCP may coalesce writes. Both frames must come out of a single feed."""
    buffer = LineBuffer()
    wire = encode(Frame(type=MessageType.PING)) + encode(Frame(type=MessageType.PONG))

    lines = buffer.feed(wire)

    assert [decode(line).type for line in lines] == [MessageType.PING, MessageType.PONG]
    assert buffer.pending == 0


def test_a_frame_split_across_reads_is_reassembled() -> None:
    """The case the plan calls out: one message arriving as two recv() calls."""
    buffer = LineBuffer()
    wire = encode(Frame(type=MessageType.MSG, sender="alice", body="split me"))
    head, tail = wire[:12], wire[12:]

    assert buffer.feed(head) == []  # not an error -- just not finished yet
    assert buffer.pending == len(head)

    lines = buffer.feed(tail)
    assert len(lines) == 1
    assert decode(lines[0]).body == "split me"
    assert buffer.pending == 0


def test_a_frame_split_one_byte_at_a_time_is_reassembled() -> None:
    buffer = LineBuffer()
    wire = encode(Frame(type=MessageType.MSG, body=CHINESE))

    lines = [line for byte in wire for line in buffer.feed(bytes([byte]))]

    assert len(lines) == 1
    assert decode(lines[0]).body == CHINESE


def test_a_multibyte_character_split_across_reads_survives() -> None:
    """A Chinese character is three UTF-8 bytes. Cut one in half and the
    buffer must hold the fragment rather than decoding it."""
    buffer = LineBuffer()
    wire = encode(Frame(type=MessageType.MSG, body=CHINESE))
    cut = wire.index("你".encode()) + 1  # mid-character

    assert buffer.feed(wire[:cut]) == []
    lines = buffer.feed(wire[cut:])

    assert decode(lines[0]).body == CHINESE


def test_trailing_carriage_return_is_tolerated() -> None:
    """Telnet on Windows sends CRLF. The trailing CR must not reach the JSON
    parser, or every hand-typed frame would be rejected as malformed."""
    buffer = LineBuffer()
    wire = encode(Frame(type=MessageType.PING)).replace(b"\n", b"\r\n")

    lines = buffer.feed(wire)

    assert decode(lines[0]).type is MessageType.PING


def test_blank_lines_are_ignored() -> None:
    assert LineBuffer().feed(b"\n\n\n") == []


def test_a_line_without_a_newline_is_capped() -> None:
    """A peer that never sends a delimiter must not be able to exhaust memory."""
    buffer = LineBuffer(max_line_bytes=64)
    with pytest.raises(ProtocolError, match="exceeded"):
        buffer.feed(b"x" * 65)
    assert buffer.pending == 0  # buffer dropped, not left to grow


# ------------------------------------------------------------ malformed ---


def test_non_json_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="not valid JSON"):
        decode("hello, is this thing on?")


def test_json_that_is_not_an_object_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode("[1, 2, 3]")


def test_a_frame_without_a_type_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="no type"):
        decode('{"v":1,"id":"abc"}')


def test_an_unknown_type_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="unknown frame type"):
        decode('{"v":1,"type":"LAUNCH_MISSILES"}')


def test_a_future_protocol_version_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="unsupported protocol version"):
        decode('{"v":99,"type":"PING"}')


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="not valid UTF-8"):
        LineBuffer().feed(b"\xff\xfe not utf-8\n")


# ----------------------------------------------------------------- text ---


@pytest.mark.parametrize("text", [CHINESE, EMOJI, "mixed 你好 🔐 text", "", " ", "a\tb"])
def test_text_survives_the_round_trip(text: str) -> None:
    buffer = LineBuffer()
    lines = buffer.feed(encode(Frame(type=MessageType.MSG, body=text)))
    assert decode(lines[0]).body == text


def test_non_ascii_is_not_escaped_on_the_wire() -> None:
    """Readable in a packet trace during the demo, and fewer bytes sent."""
    wire = encode(Frame(type=MessageType.MSG, body=CHINESE))
    assert CHINESE.encode("utf-8") in wire
    assert rb"\u" not in wire
