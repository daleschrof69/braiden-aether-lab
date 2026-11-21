import struct
import uuid
import zlib

import pytest

from aetherlab.ops import gpt


def make_gpt(sector=512, entries=4):
    g = gpt.Gpt(sector, 0)
    g.first_usable = 34
    g.last_usable = 34 + entries * 16
    for i in range(entries):
        g.add(f"part{i}", 34 + i * 16, 34 + i * 16 + 15)
    return g


def test_roundtrip():
    g = make_gpt()
    front, (backup_hdr, table) = gpt.build(g)
    parsed = gpt.parse(front, 512, 0)
    assert [e.name for e in parsed.entries] == [e.name for e in g.entries]
    assert parsed.disk_guid == g.disk_guid


def test_crc_enforced():
    g = make_gpt()
    front, _ = gpt.build(g)
    blob = bytearray(front)
    blob[1024 + 200] ^= 0xFF          # corrupt entry table
    with pytest.raises(gpt.PartitionError):
        gpt.parse(bytes(blob), 512, 0)


def test_missing_signature():
    with pytest.raises(gpt.PartitionError):
        gpt.parse(b"\x00" * 4096, 512, 0)


def test_by_name_and_slot():
    g = gpt.Gpt(512, 0)
    g.add("boot_a", 34, 49)
    g.add("boot_b", 50, 65)
    assert g.by_name("boot", "a").first_lba == 34
    assert g.by_name("boot", "b").first_lba == 50
    assert g.by_name("BOOT").first_lba == 34


def test_pmbr():
    m = gpt.pmbr()
    assert len(m) == 512
    assert m[510:512] == b"\x55\xAA"
    assert m[446 + 4] == 0xEE
