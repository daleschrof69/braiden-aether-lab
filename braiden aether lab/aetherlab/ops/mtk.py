"""High-level MediaTek operations.

Flow: BROM handshake -> DA upload -> jump -> DA protocol. Partition access on
MTK is by name via a raw directory scan of the GPT, because there is no
Firehose-style LUN abstraction; LUN is fixed at 0.

MTK secure boot (SBC/SLA) will refuse an unsigned DA with 0xC1 or silence
after the size word. If handshake() succeeds but send_da() stalls, that is
secure boot, not a cable.
"""
import os

from .. import config
from ..errors import PartitionError, ProtocolError
from ..proto.mtk_brom import Brom
from ..proto.mtk_da import MtkDa
from ..transport.usb import find_mtk
from . import gpt

DA_ADDR = 0x40001000
PAGE = 0x10000


class MtkSession:
    def __init__(self, log, soc, da_addr=None):
        self.log = log
        self.soc = soc
        self.da_addr = da_addr or DA_ADDR
        self.t = None
        self.da = None
        self._gpt = None

    def __enter__(self):
        da = config.find_da(self.soc).read_bytes()
        self.t = find_mtk()
        self.log.info("mtk: attached")
        brom = Brom(self.t, self.log)
        brom.handshake()
        self.log.info("mtk: uploading DA (%d bytes)", len(da))
        brom.send_da(da)
        brom.jump_da(self.da_addr)
        self.da = MtkDa(self.t, self.log)
        self.da.target_config()
        return self

    def __exit__(self, *a):
        try:
            if self.da:
                self.da.jump_bl(0x0)
        except Exception:
            pass
        if self.t:
            self.t.close()

    def info(self):
        return {
            "hw_code": f"{self.da.hw_code():#06x}",
            "hw_sw_ver": self.da.hw_sw_ver().hex(),
            "bl_ver": self.da.bl_ver().hex(),
            "brom": self.da.brom_version(),
        }

    def gpt(self):
        if self._gpt is None:
            raw = self.da.read32(0, 34 * 512)
            self._gpt = gpt.parse(raw, 512, 0)
        return self._gpt

    def part(self, name, slot=None):
        return self.gpt().by_name(name, slot)

    def dump(self, name, dest, slot=None):
        e = self.part(name, slot)
        nbytes = e.sectors * 512
        self.log.info("mtk: dumping %s (%d bytes)", e.name, nbytes)
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            done = 0
            while done < nbytes:
                step = min(PAGE, nbytes - done)
                f.write(self.da.read32(e.first_lba * 512 + done, step))
                done += step
        return dest

    def write(self, name, src, slot=None, force=False):
        e = self.part(name, slot)
        data = open(src, "rb").read()
        need = (len(data) + 511) // 512
        if need > e.sectors and not force:
            raise PartitionError(f"{need} sectors > {e.sectors}, use --force")
        self.log.info("mtk: writing %s (%d bytes)", e.name, len(data))
        base = e.first_lba * 512
        for off in range(0, len(data), PAGE):
            self.da.write32(base + off, data[off:off + PAGE])

    def erase(self, name, slot=None):
        e = self.part(name, slot)
        zeros = b"\x00" * PAGE
        base = e.first_lba * 512
        total = e.sectors * 512
        self.log.info("mtk: zeroing %s", e.name)
        for off in range(0, total, PAGE):
            self.da.write32(base + off, zeros[:min(PAGE, total - off)])

    def frp_erase(self):
        hit = []
        for name in config.MTK_FRP_PARTITIONS:
            try:
                self.erase(name)
                hit.append(name)
            except PartitionError:
                continue
        if not hit:
            raise PartitionError("no FRP partition found")
        return hit

    def read_nvram(self, dest):
        for name in ("nvram", "nvdata", "protect1", "protect2"):
            try:
                self.dump(name, os.path.join(dest, f"{name}.bin"))
            except PartitionError:
                pass

    def write_nvram(self, src_dir):
        for name in ("nvram", "nvdata", "protect1", "protect2"):
            p = os.path.join(src_dir, f"{name}.bin")
            if os.path.isfile(p):
                self.write(name, p, force=True)

    def unlock_network(self):
        """Clear the SIM-lock bytes in `nvdata`. Offsets vary by SoC; this
        zeroes the two known lock words on MT67xx nvdata headers."""
        e = self.part("nvdata")
        head = self.da.read32(e.first_lba * 512, 0x100)
        if head[0x10] == 0 and head[0x14] == 0:
            self.log.info("mtk: already network-unlocked")
            return False
        self.da.write32(e.first_lba * 512 + 0x10, b"\x00" * 8)
        self.log.info("mtk: lock words cleared")
        return True
