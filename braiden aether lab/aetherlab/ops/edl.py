"""High-level Qualcomm EDL operations.

Session shape: Sahara handshake -> push Firehose loader -> Firehose configure.
Everything after that is sector I/O against a GPT. Partition names resolve
through the GPT, so a bad name fails before a single sector is written.

Two safety rails worth keeping: `read` never writes, and `write` refuses to
touch a partition whose sector count is smaller than the payload unless
`--force` is passed. Half a super.img on a `boot` partition bricks a boot loop
in a way that looks like a dead SoC.
"""
import hashlib
import os

from .. import config
from ..errors import AuthError, PartitionError
from ..proto import firehose, sahara
from ..transport.usb import find_edl
from . import gpt


class EdlSession:
    def __init__(self, log, chipset=None, image_id=None, sector_size=None,
                 memory_name="ufs"):
        self.log = log
        self.chipset = chipset
        self.image_id = image_id
        self.sector_size = sector_size
        self.memory_name = memory_name
        self.t = None
        self.sahara = None
        self.fh = None
        self._gpts = {}

    def __enter__(self):
        self.t = find_edl()
        self.log.info("edl: attached")
        self.sahara = sahara.Sahara(self.t, self.log)
        hello = self.sahara.handshake()
        chip = self.chipset or f"{hello.chipset_id:08x}"
        loader = config.find_firehose(chip)
        blob = loader.read_bytes()
        self.log.info("edl: pushing %s (%d bytes)", loader.name, len(blob))
        self.sahara.send_image(self.image_id or config.DEFAULT_IMAGE_ID, blob)
        self.fh = firehose.Firehose(self.t, self.log)
        self.fh.configure(self.memory_name, self.sector_size)
        return self

    def __exit__(self, *a):
        try:
            if self.fh:
                self.fh.power("reset")
        finally:
            if self.t:
                self.t.close()

    # ---- partition table -------------------------------------------------
    def gpt(self, lun=0):
        if lun not in self._gpts:
            raw = self.fh.read(lun, 0, 34 * self.fh.sector_size)
            self._gpts[lun] = gpt.parse(raw, self.fh.sector_size, lun)
            self.log.info("edl: lun %d has %d partitions", lun, len(self._gpts[lun].entries))
        return self._gpts[lun]

    def list_partitions(self):
        out = []
        for lun in range(6):
            try:
                g = self.gpt(lun)
            except PartitionError:
                break
            for e in g.entries:
                out.append((lun, e))
        return out

    def part(self, name, lun=None, slot=None):
        if lun is not None:
            return lun, self.gpt(lun).by_name(name, slot)
        for cand in range(6):
            try:
                return cand, self.gpt(cand).by_name(name, slot)
            except PartitionError:
                continue
        raise PartitionError(f"partition {name!r} not found on any LUN")

    # ---- data movement ---------------------------------------------------
    def dump(self, name, dest, slot=None, lun=None):
        lun, e = self.part(name, lun, slot)
        nbytes = e.sectors * self.fh.sector_size
        self.log.info("edl: dumping %s (%d bytes)", e.name, nbytes)
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        digest = hashlib.sha256()
        with open(dest, "wb") as f:
            done = 0
            while done < nbytes:
                step = min(self.fh.max_payload - 512, nbytes - done)
                step = (step // self.fh.sector_size) * self.fh.sector_size or self.fh.sector_size
                blob = self.fh.read(lun, e.first_lba + done // self.fh.sector_size, step)
                f.write(blob)
                digest.update(blob)
                done += len(blob)
        self.log.info("edl: wrote %s sha256=%s", dest, digest.hexdigest()[:16])
        return dest

    def write(self, name, src, slot=None, lun=None, force=False):
        lun, e = self.part(name, lun, slot)
        data = open(src, "rb").read()
        need = (len(data) + self.fh.sector_size - 1) // self.fh.sector_size
        if need > e.sectors and not force:
            raise PartitionError(
                f"{src} needs {need} sectors, {e.name} is {e.sectors}. --force to override")
        self.log.info("edl: writing %s (%d bytes)", e.name, len(data))
        self.fh.program(lun, e.first_lba, data, os.path.basename(src))

    def erase(self, name, slot=None, lun=None):
        lun, e = self.part(name, lun, slot)
        self.log.info("edl: erasing %s (%d sectors)", e.name, e.sectors)
        self.fh.erase(lun, e.first_lba, e.sectors * self.fh.sector_size)

    def write_raw(self, lun, start_sector, src):
        data = open(src, "rb").read()
        self.fh.program(lun, start_sector, data, os.path.basename(src))

    # ---- FRP -------------------------------------------------------------
    def frp_erase(self):
        hit = []
        for name in config.QUALCOMM_FRP_PARTITIONS:
            try:
                lun, e = self.part(name)
            except PartitionError:
                continue
            self.fh.erase(lun, e.first_lba, e.sectors * self.fh.sector_size)
            hit.append(e.name)
            self.log.info("edl: erased %s", e.name)
        if not hit:
            raise PartitionError("no FRP-bearing partition present")
        return hit

    # ---- rawprogram flow -------------------------------------------------
    def flash_rawprogram(self, xml_path, image_dir, patch_xml=None):
        from .firmware import parse_rawprogram, patch_ops
        recs = parse_rawprogram(xml_path)
        self.log.info("edl: %d records in %s", len(recs), xml_path)
        for r in recs:
            src = os.path.join(image_dir, r.filename)
            if not os.path.isfile(src):
                self.log.warning("edl: missing %s, skipping %s", src, r.partition_name)
                continue
            self.log.info("edl: %s @ lun%d sector %d", r.partition_name, r.lun, r.start_sector)
            self.write_raw(r.lun, r.start_sector, src)
        if patch_xml and os.path.isfile(patch_xml):
            for lun, off, val, size in patch_ops(patch_xml):
                self.log.info("edl: patch lun%d off %d = %s", lun, off, val)
                self.fh.patch(lun, off, val, size)

    def unlock_bootloader(self):
        """Clear the `devinfo` bootloader-unlock flag. Layout is
        Qualcomm-generic: byte 0 of devinfo, low nibble, set to 1."""
        lun, e = self.part("devinfo")
        blob = self.fh.read(lun, e.first_lba, self.fh.sector_size)
        if blob[0] & 0x01:
            self.log.info("edl: bootloader already unlocked")
            return False
        self.fh.program(lun, e.first_lba, bytes([blob[0] | 0x01]))
        self.log.info("edl: devinfo flag set")
        return True
