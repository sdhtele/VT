from __future__ import annotations

from abc import ABCMeta, abstractmethod
from pathlib import Path

try:
    # this was tested to work with protobuf 3, but it's an internal API (any varint decoder might work)
    from google.protobuf.internal.decoder import _DecodeVarint as _di  # type: ignore[attr-defined]
except ImportError:
    # this is generic and does not depend on pb internals,
    # however it will decode "larger" possible numbers than pb decoder which has them fixed
    def leb128_decode(buffer: bytes, pos: int, limit: int = 64) -> tuple[int, int]:
        result = 0
        shift = 0
        while True:
            b = buffer[pos]
            pos += 1
            result |= ((b & 0x7F) << shift)
            if not b & 0x80:
                return result, pos
            shift += 7
            if shift > limit:
                raise Exception("integer too large, shift: {}".format(shift))

    _di = leb128_decode


class FromFileMixin(metaclass=ABCMeta):
    @abstractmethod
    def __init__(self, buf: bytes):
        ...

    @classmethod
    def from_file(cls, filename: Path) -> FromFileMixin:
        """Load given a filename"""
        return cls(filename.read_bytes())


# the signatures use a format internally similar to
# protobuf's encoding, but without wire types
class VariableReader(FromFileMixin):
    """Protobuf-like encoding reader"""

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0
        self.size = len(buf)

    def read_int(self) -> int:
        """Read a variable length integer"""
        # _DecodeVarint will take care of out of range errors
        val, nextpos = _di(self.buf, self.pos)
        self.pos = nextpos
        return val

    def read_bytes_raw(self, size: int) -> bytes:
        """Read size bytes"""
        b = self.buf[self.pos:self.pos + size]
        self.pos += size
        return b

    def read_bytes(self) -> bytes:
        """Read a bytes object"""
        size = self.read_int()
        return self.read_bytes_raw(size)

    def is_end(self) -> bool:
        return self.size == self.pos


class TaggedReader(VariableReader):
    """Tagged reader, needed for implementing a Widevine signature reader"""

    def read_tag(self) -> tuple[int, bytes]:
        """Read a tagged buffer"""
        return self.read_int(), self.read_bytes()

    def read_all_tags(self, max_tag: int = 3) -> dict[int, bytes]:
        tags = {}
        while not self.is_end():
            tag, bytes_ = self.read_tag()
            if tag > max_tag:
                raise IndexError("tag out of bound: got {}, max {}".format(tag, max_tag))

            tags[tag] = bytes_
        return tags


class WidevineSignatureReader(FromFileMixin):
    """Parses a Widevine .sig signature file."""

    SIGNER_TAG = 1
    SIGNATURE_TAG = 2
    ISMAINEXE_TAG = 3

    def __init__(self, buf: bytes):
        reader = TaggedReader(buf)
        self.version = reader.read_int()
        if self.version != 0:
            raise Exception("Unsupported signature format version {}".format(self.version))
        self.tags = reader.read_all_tags()

        self.signer = self.tags[self.SIGNER_TAG]
        self.signature = self.tags[self.SIGNATURE_TAG]

        extra = self.tags[self.ISMAINEXE_TAG]
        if len(extra) != 1 or (extra[0] > 1):
            raise Exception(f"Unexpected 'ismainexe' field value (not '\\x00' or '\\x01'), please check: {extra!r}")

        self.mainexe = bool(extra[0])

    @classmethod
    def get_tags(cls, filename: Path) -> dict[int, bytes]:
        """Return a dictionary of each tag in the signature file"""
        return cls.from_file(filename).tags
