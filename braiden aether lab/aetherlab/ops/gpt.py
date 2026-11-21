"""GUID Partition Table parse, rebuild, and locate.

GPT on phones is normal GPT with the usual protections removed: the header is
at LBA 1, entries at LBA 2, and a backup header sits in the last LBA. Samsung
and most Qualcomm parts still ship a protective MBR at LBA 0. Sector size is
512 on eMMC and 4096 on UFS - read it from the loader, never assume.

Partition names are the handle everything else uses, so resolve by name, not by
index. `by_name` is case-insensitive and strips the `_a`/`_b` slot suffix when
asked, because A/B devices will otherwise silently give you the wrong slot.
"""
import struct
import uuid
import zlib

from ..errors import PartitionError

SIG = b"EFI PART"
REVISION = 0x00010000
UNUSED = "00000000-0000-0000-0000-000000000000"
MAX_ENTRIES = 256


def _guid(raw):
    return str(uuid.UUID(bytes_le=raw))


def _unguid(s):
    return uuid.UUID(s).bytes_le


class Entry:
    __slots__ = ("index", "type_guid", "guid", "first_lba", "last_lba",
                 "attrs", "name")

    def __init__(self, index, raw, entry_size):
        if len(raw) < entry_size:
            raw = raw.ljust(entry_size, b"\x00")
        self.index = index
        self.type_guid = _guid(raw[0:16])
        self.guid = _guid(raw[16:32])
        self.first_lba, self.last_lba, self.attrs = struct.unpack_from("<QQQ", raw, 32)
        self.name = raw[56:128].decode("utf-16-le", "replace").split("\x00")[0]

    @property
    def used(self):
        return self.type_guid != UNUSED

    @property
    def sectors(self):
        return self.last_lba - self.first_lba + 1

    @property
    def base_name(self):
        n = self.name
        for suf in ("_a", "_b"):
            if n.endswith(suf):
                return n[:-2]
        return n

    def __repr__(self):
        return (f"<{self.name!r} {self.first_lba}-{self.last_lba} "
                f"({self.sectors} sec) type={self.type_guid[:8]}>")


class Gpt:
    def __init__(self, sector_size=512, lun=0):
        self.sector_size = sector_size
        self.lun = lun
        self.disk_guid = str(uuid.uuid4())
        self.first_usable = 34
        self.last_usable = 0
        self.entries = []

    @property
    def total_sectors(self):
        return self.last_usable + 34

    def by_name(self, name, slot=None):
        want = name.lower()
        if slot:
            want = f"{want}_{slot}".lower()
        for e in self.entries:
            if e.used and (e.name.lower() == want
                           or (slot is None and e.base_name.lower() == want)):
                return e
        raise PartitionError(f"no partition named {name!r} on lun {self.lun}")

    def add(self, name, first_lba, last_lba, type_guid=None):
        e = Entry(0, b"\x00" * 128, 128)
        e.type_guid = type_guid or "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
        e.guid = str(uuid.uuid4())
        e.first_lba, e.last_lba, e.attrs = first_lba, last_lba, 0
        e.name = name
        e.index = len(self.entries) + 1
        self.entries.append(e)
        return e


def parse(blob, sector_size=512, lun=0) -> Gpt:
    hdr = blob[sector_size:sector_size * 2]
    if hdr[:8] != SIG:
        raise PartitionError(f"lun {lun}: no GPT signature at LBA 1")
    (_rev, _hsize, hcrc, _rsvd, cur, backup, first_usable, last_usable,
     disk_guid, entries_lba, num, entry_size,
     ecrc) = struct.unpack_from("<IIIIQQQQ16sQIII", hdr, 8)
    header_for_crc = bytearray(hdr)
    struct.pack_into("<I", header_for_crc, 16, 0)
    if zlib.crc32(header_for_crc[:92]) & 0xFFFFFFFF != hcrc:
        raise PartitionError(f"lun {lun}: header CRC mismatch")
    if entry_size < 128 or entry_size > 512:
        raise PartitionError(f"lun {lun}: absurd entry size {entry_size}")
    if num > MAX_ENTRIES:
        raise PartitionError(f"lun {lun}: {num} entries")

    g = Gpt(sector_size, lun)
    g.disk_guid = _guid(disk_guid)
    g.first_usable = first_usable
    g.last_usable = last_usable

    base = entries_lba * sector_size
    table = blob[base:base + num * entry_size]
    if len(table) < num * entry_size:
        raise PartitionError(f"lun {lun}: truncated entry table")
    if zlib.crc32(table) & 0xFFFFFFFF != ecrc:
        raise PartitionError(f"lun {lun}: entry table CRC mismatch")

    for i in range(num):
        e = Entry(i + 1, table[i * entry_size:(i + 1) * entry_size], entry_size)
        if e.used:
            g.entries.append(e)
    return g


def build(g: Gpt) -> bytes:
    """Serialize primary header, entry table, and backup. Returns the whole
    front+back image; caller writes the ranges it needs."""
    s = g.sector_size
    num = max(128, len(g.entries) + 1)
    table = bytearray(num * 128)
    for i, e in enumerate(g.entries):
        raw = (_unguid(e.type_guid) + _unguid(e.guid)
               + struct.pack("<QQQ", e.first_lba, e.last_lba, e.attrs)
               + e.name.encode("utf-16-le").ljust(72, b"\x00")[:72])
        table[i * 128:(i + 1) * 128] = raw

    ecrc = zlib.crc32(bytes(table)) & 0xFFFFFFFF
    last = g.last_usable + 34

    def hdr_at(current, backup_lba, entries_lba):
        raw = bytearray(92)
        raw[0:8] = SIG
        struct.pack_into("<IIIIQQQQ16sQIII", raw, 8, REVISION, 92, 0, 0,
                         current, backup_lba, g.first_usable, g.last_usable,
                         _unguid(g.disk_guid), entries_lba, num, 128, ecrc)
        struct.pack_into("<I", raw, 16, zlib.crc32(bytes(raw)) & 0xFFFFFFFF)
        return bytes(raw).ljust(s, b"\x00")

    out = bytearray(b"\x00" * s)              # LBA 0 protective MBR slot
    out += hdr_at(1, last, 2)
    out += table
    return bytes(out), (hdr_at(last, 1, last - num), table)


def pmbr() -> bytes:
    mbr = bytearray(512)
    struct.pack_into("<I", mbr, 440, 0x12345678)
    p = 446
    mbr[p] = 0x00
    mbr[p + 4] = 0xEE
    struct.pack_into("<II", mbr, p + 8, 1, 0xFFFFFFFF)
    mbr[510:512] = b"\x55\xAA"
    return bytes(mbr)
