"""Firmware package handling.

Samsung ships `.tar.md5`: an ordinary tar with a trailing `\n` + 32 hex chars
appended after the end-of-archive marker. You must verify and then strip the
suffix before the tar is valid - `tarfile` will otherwise hand you a truncated
member or a bogus entry. The inner tars (AP/BL/CP/CSC) are themselves tars of
`.img`/`.mbn`/`.bin` payloads plus a `*.pit`.

Qualcomm packages ship `rawprogram*.xml` + `patch*.xml` + the raw images.
"""
import hashlib
import io
import os
import tarfile
import xml.etree.ElementTree as ET

from ..errors import AetherError, PartitionError

MD5_SUFFIX = 32


class PackageError(AetherError):
    pass


def verify_md5(path) -> str:
    """Verify a `.tar.md5`. Returns the digest. Raises on mismatch."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < MD5_SUFFIX + 2:
        raise PackageError(f"{path}: too short to carry an MD5")
    claimed = data[-MD5_SUFFIX:].decode("ascii", "replace").strip().lower()
    body = data[:-(MD5_SUFFIX + 1)]
    actual = hashlib.md5(body).hexdigest()
    if claimed != actual:
        raise PackageError(f"{path}: MD5 mismatch (claim {claimed} actual {actual})")
    return actual


def strip_md5(path) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if path.endswith(".md5") and len(data) > MD5_SUFFIX + 2:
        return data[:-(MD5_SUFFIX + 1)]
    return data


def members(path):
    """Yield (TarInfo, fileobj) from a possibly md5-suffixed tar."""
    blob = strip_md5(path)
    tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:")
    for m in tf.getmembers():
        if m.isfile():
            yield m, tf.extractfile(m)


def extract(path, outdir):
    os.makedirs(outdir, exist_ok=True)
    made = []
    for m, fh in members(path):
        base = os.path.basename(m.name)
        if not base:
            continue
        dest = os.path.join(outdir, base)
        with open(dest, "wb") as out:
            out.write(fh.read())
        made.append(dest)
    return made


def classify(path):
    """Samsung slot from filename: BL, AP, CP, CSC, HOME_CSC, USERDATA."""
    base = os.path.basename(path).upper()
    for tag in ("HOME_CSC", "CSC", "BL", "AP", "CP", "USERDATA"):
        if base.startswith(tag):
            return tag
    return "OTHER"


class RawProgram:
    """One <program> record from a Qualcomm rawprogram XML."""

    __slots__ = ("lun", "start_sector", "num_sectors", "filename",
                 "partition_name", "sector_size")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __repr__(self):
        return (f"<{self.partition_name} lun{self.lun} "
                f"sector {self.start_sector}+{self.num_sectors}>")


def parse_rawprogram(path):
    """Parse rawprogram*.xml into an ordered list, skipping blank entries."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise PackageError(f"{path}: {e}") from e
    out = []
    for node in root.iter("program"):
        fname = (node.get("filename") or "").strip()
        if not fname:
            continue
        start = node.get("start_sector") or "0"
        if start.upper().startswith("NUM_DISK"):
            continue
        out.append(RawProgram(
            lun=int(node.get("physical_partition_number") or 0),
            start_sector=int(start),
            num_sectors=int(node.get("num_partitions") and 0
                            or node.get("size_in_KB") and 0 or 0) or None,
            filename=fname,
            partition_name=(node.get("label") or fname).strip(),
            sector_size=int(node.get("SECTOR_SIZE_IN_BYTES") or 4096)))
    return out


def patch_ops(path):
    """Parse patch*.xml into (lun, byte_offset, value, size) tuples."""
    out = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return out
    for node in root.iter("patch"):
        out.append((
            int(node.get("physical_partition_number") or 0),
            int(node.get("byte_offset") or 0),
            node.get("value") or "0x00",
            int(node.get("size_in_bytes") or 1),
        ))
    return out
