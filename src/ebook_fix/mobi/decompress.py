"""
ebook_fix.mobi.decompress

Turns the compressed text records of a MOBI-family book back into the
book's raw markup. Two schemes matter in practice:

- PalmDOC (compression type 2): a simple LZ77-style scheme. Every MOBI
  file written by Calibre, Kindle Previewer, or Amazon's own tools
  uses this one, including both the real MOBI7 and AZW3 samples in
  examples/.
- HUFF/CDIC (compression type 17480): an older Huffman-plus-dictionary
  scheme used by some early Mobipocket-made books. Written from the
  documented format rather than confirmed against a real file -- no
  HUFF/CDIC sample exists yet. `convert.py` cross-checks the total
  decompressed length against the length the file's own header claims,
  so a wrong result here is reported as an error instead of quietly
  producing a damaged book.

Also strips the "trailing entries" a text record can carry after its
compressed data (multibyte-character overlap bytes and Kindle index
bookkeeping), which have to come off *before* decompression.
"""
from __future__ import annotations

import struct


class DecompressError(ValueError):
    """The text data couldn't be decompressed (corrupt or unsupported)."""


# ---------------------------------------------------------------------
# PalmDOC
# ---------------------------------------------------------------------

def palmdoc_decompress(data: bytes) -> bytes:
    """Decompresses one PalmDOC-compressed text record.

    Byte meanings, by first-byte value:
      0x00, 0x09-0x7F   copied through as-is
      0x01-0x08         the next N bytes are copied through as-is
      0x80-0xBF         a two-byte back-reference: 11 bits of distance,
                        3 bits of length (length + 3)
      0xC0-0xFF         a space followed by (byte XOR 0x80)
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0 or 0x09 <= c <= 0x7F:
            out.append(c)
        elif 0x01 <= c <= 0x08:
            out += data[i:i + c]
            i += c
        elif c >= 0xC0:
            out.append(0x20)
            out.append(c ^ 0x80)
        else:
            if i >= n:
                break
            pair = (c << 8) | data[i]
            i += 1
            distance = (pair & 0x3FFF) >> 3
            length = (pair & 0x07) + 3
            if distance == 0 or distance > len(out):
                raise DecompressError(
                    "Corrupt PalmDOC data: a back-reference points before "
                    "the start of the text."
                )
            for _ in range(length):
                out.append(out[-distance])
    return bytes(out)


# ---------------------------------------------------------------------
# Trailing entries
# ---------------------------------------------------------------------

def _trailing_entry_size(data: bytes, end: int) -> int:
    """Size (including its own length bytes) of the trailing entry whose
    last byte sits at data[end - 1]. The length is stored backwards as
    a variable-width integer, 7 bits per byte, where the byte carrying
    the 0x80 flag is the *first* one of the number."""
    bitpos = 0
    result = 0
    pos = end
    while True:
        value = data[pos - 1]
        result |= (value & 0x7F) << bitpos
        bitpos += 7
        pos -= 1
        if (value & 0x80) or bitpos >= 28 or pos == 0:
            return result


def trailing_bytes(record: bytes, extra_data_flags: int) -> int:
    """How many bytes at the end of a text record are trailing entries
    rather than compressed text, given the header's extra-data flags."""
    total = 0
    flags = extra_data_flags >> 1
    while flags:
        if flags & 1:
            total += _trailing_entry_size(record, len(record) - total)
        flags >>= 1
    if extra_data_flags & 1:
        total += (record[len(record) - total - 1] & 0x03) + 1
    return total


def strip_trailing_entries(record: bytes, extra_data_flags: int) -> bytes:
    if not extra_data_flags:
        return record
    cut = trailing_bytes(record, extra_data_flags)
    if cut < 0 or cut > len(record):
        raise DecompressError("Corrupt text record: trailing data is longer than the record.")
    return record[:len(record) - cut]


# ---------------------------------------------------------------------
# HUFF/CDIC
# ---------------------------------------------------------------------

class HuffCdicReader:
    """Decoder for the HUFF/CDIC scheme. Feed it the HUFF record and
    every CDIC record (in order), then call `unpack` once per text
    record."""

    def __init__(self, huff: bytes, cdics: list[bytes]):
        self._load_huff(huff)
        self.dictionary: list = []
        for cdic in cdics:
            self._load_cdic(cdic)

    def _load_huff(self, huff: bytes) -> None:
        if huff[0:8] != b"HUFF\x00\x00\x00\x18":
            raise DecompressError("Invalid HUFF record header.")
        off1, off2 = struct.unpack_from(">LL", huff, 8)

        def dict1_unpack(value: int):
            codelen, term, maxcode = value & 0x1F, value & 0x80, value >> 8
            if codelen == 0:
                raise DecompressError("Invalid HUFF table (zero-length code).")
            maxcode = ((maxcode + 1) << (32 - codelen)) - 1
            return codelen, term, maxcode

        self.dict1 = [dict1_unpack(v) for v in struct.unpack_from(">256L", huff, off1)]

        dict2 = struct.unpack_from(">64L", huff, off2)
        self.mincode = []
        self.maxcode = []
        for codelen, mincode in enumerate((0,) + dict2[0::2]):
            self.mincode.append(mincode << (32 - codelen))
        for codelen, maxcode in enumerate((0,) + dict2[1::2]):
            self.maxcode.append(((maxcode + 1) << (32 - codelen)) - 1)

    def _load_cdic(self, cdic: bytes) -> None:
        if cdic[0:8] != b"CDIC\x00\x00\x00\x10":
            raise DecompressError("Invalid CDIC record header.")
        phrases, bits = struct.unpack_from(">LL", cdic, 8)
        count = min(1 << bits, phrases - len(self.dictionary))
        offsets = struct.unpack_from(">%dH" % count, cdic, 16)
        for off in offsets:
            (blen,) = struct.unpack_from(">H", cdic, 16 + off)
            chunk = cdic[18 + off:18 + off + (blen & 0x7FFF)]
            self.dictionary.append((chunk, blen & 0x8000))

    def unpack(self, data: bytes) -> bytes:
        bitsleft = len(data) * 8
        data = data + b"\x00" * 8
        pos = 0
        (x,) = struct.unpack_from(">Q", data, pos)
        n = 32
        out = bytearray()
        while True:
            if n <= 0:
                pos += 4
                (x,) = struct.unpack_from(">Q", data, pos)
                n += 32
            code = (x >> n) & 0xFFFFFFFF
            codelen, term, maxcode = self.dict1[code >> 24]
            if not term:
                while code < self.mincode[codelen]:
                    codelen += 1
                maxcode = self.maxcode[codelen]
            n -= codelen
            bitsleft -= codelen
            if bitsleft < 0:
                break
            index = (maxcode - code) >> (32 - codelen)
            try:
                chunk, is_final = self.dictionary[index]
            except IndexError:
                raise DecompressError("Corrupt HUFF/CDIC data: dictionary index out of range.")
            if not is_final:
                # A dictionary entry that is itself compressed; expand it
                # once and remember the result.
                self.dictionary[index] = (b"", 1)
                chunk = self.unpack(chunk)
                self.dictionary[index] = (chunk, 1)
            out += chunk
        return bytes(out)
