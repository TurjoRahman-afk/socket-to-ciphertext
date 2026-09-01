"""Turning frames into bytes and back.

Two separate jobs live here, and keeping them separate is what makes both
testable:

    LineBuffer  bytes  ->  complete lines      (the TCP framing problem)
    decode      line   ->  Frame               (the parsing problem)
    encode      Frame  ->  bytes

TCP is a byte stream with no message boundaries. One recv() may return half a
frame, or three frames at once, or a frame split in the middle of a multi-byte
UTF-8 character. LineBuffer is the only thing in the project that has to care.
"""

from __future__ import annotations

import json

from im.common.frames import Frame, ProtocolError

#: Frames are newline-delimited, so the newline may never appear inside one.
DELIMITER = b"\n"

#: Refuse to buffer more than this without seeing a delimiter. Without a cap, a
#: peer that opens a connection and streams bytes without ever sending a
#: newline would grow the buffer until the server runs out of memory.
MAX_LINE_BYTES = 1024 * 1024


def encode(frame: Frame) -> bytes:
    """Serialise a frame to one UTF-8 line, newline included.

    ensure_ascii is off so Chinese characters and emoji travel as themselves
    rather than as escape sequences; separators are compact because every byte
    is sent once per message.
    """
    text = json.dumps(frame.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8") + DELIMITER


def decode(line: str) -> Frame:
    """Parse one line into a Frame, or raise ProtocolError."""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"line is not valid JSON: {exc.msg}") from exc
    return Frame.from_dict(raw)


class LineBuffer:
    """Accumulates received bytes and hands back complete lines.

    One instance per connection, used only by that connection's reader thread,
    so it needs no lock of its own.
    """

    __slots__ = ("_buffer", "_max_line_bytes")

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._buffer = bytearray()
        self._max_line_bytes = max_line_bytes

    def feed(self, chunk: bytes) -> list[str]:
        """Add received bytes; return whatever complete lines that produced.

        Returns an empty list when the chunk did not finish a line -- that is
        normal, not an error. The incomplete remainder is kept for next time.
        Decoding happens per line, so a chunk that splits a multi-byte
        character is handled correctly: the partial bytes simply stay in the
        buffer until the rest arrives.
        """
        self._buffer.extend(chunk)

        lines: list[str] = []
        while True:
            index = self._buffer.find(DELIMITER)
            if index == -1:
                break
            raw = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            text = self._decode_utf8(raw).rstrip("\r")  # tolerate CRLF from telnet
            if text:
                lines.append(text)

        if len(self._buffer) > self._max_line_bytes:
            self._buffer.clear()
            raise ProtocolError(f"line exceeded {self._max_line_bytes} bytes without a newline")

        return lines

    @property
    def pending(self) -> int:
        """Bytes held for an unfinished line. Useful in tests and logs."""
        return len(self._buffer)

    @staticmethod
    def _decode_utf8(raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("line is not valid UTF-8") from exc
