"""Qualcomm Firehose - the actual flashing protocol.

XML over bulk. Every request is an XML element, every response is
`<response value="ACK|NAK" .../>`. Program and read carry raw payload appended
to the element in the same transfer, sized to whole sectors, and the transfer
must stay under MaxPayloadSizeToTargetInBytes (negotiated at configure; 1 MiB
is the ceiling most loaders accept).

Sector size is load-bearing: eMMC loaders use 512, UFS use 4096. Get it wrong
and you get an ACK followed by silent corruption, because the loader trusts you.

UFS needs `setbootablestoragedrive` (LUN select) before configure, and
physical_partition_number carries the LUN afterwards.
"""
import time
import xml.etree.ElementTree as ET

from ..errors import AuthError, DeviceGone, ProtocolError
from ..transport.base import Transport

DEFAULT_SECTOR = 4096
MAX_PAYLOAD = 0x100000


def _extract_logs(text):
    out, i = [], 0
    while True:
        s = text.find("<log ", i)
        if s == -1:
            return out
        e = text.find("/>", s)
        if e == -1:
            return out
        out.append(text[s:e + 2])
        i = e + 2


class Firehose:
    def __init__(self, t: Transport, log, max_payload=MAX_PAYLOAD):
        self.t = t
        self.log = log
        self.max_payload = max_payload
        self.sector_size = DEFAULT_SECTOR
        self.memory_name = "ufs"
        self._rx = bytearray()

    def _read_until_response(self, timeout_ms=15000):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            chunk = self.t.read(65536, 500)
            if chunk:
                self._rx += chunk
            text = self._rx.decode("utf-8", "replace")
            if 'value="ACK"' in text or 'value="NAK"' in text:
                end = text.find("/>", text.find("<response"))
                if end != -1:
                    full = text[:end + 2]
                    self._rx = bytearray(text[end + 2:].encode())
                    return full
            for frag in _extract_logs(text):
                self.log.debug("firehose[dev]: %s", frag)
        raise ProtocolError("firehose: response timeout")

    def cmd(self, xml, payload=b"", allow_nak=False):
        if len(xml.encode()) + len(payload) > self.max_payload:
            raise ProtocolError("firehose: request exceeds negotiated payload")
        self.log.debug("firehose[host]: %s", xml.strip())
        self.t.write(xml.encode() + payload)
        resp = self._read_until_response()
        if 'value="NAK"' in resp and not allow_nak:
            raise ProtocolError(f"firehose: NAK for {xml.split()[0]}: {resp}")
        return resp

    def configure(self, memory_name=None, sector_size=None):
        self.memory_name = memory_name or self.memory_name
        self.sector_size = sector_size or self.sector_size
        if self.memory_name == "ufs":
            # LUN 1 is userdata on most UFS parts; LUN 0 is boot.
            self.cmd('<setbootablestoragedrive value="1"/>', allow_nak=True)
        resp = self.cmd(
            f'<configure MemoryName="{self.memory_name}" Verbose="0" '
            f'AlwaysValidate="0" MaxDigestTableSizeInBytes="8192" '
            f'MaxPayloadSizeToTargetInBytes="{MAX_PAYLOAD}" '
            f'ZlpAwareHost="1" SkipStorageInit="0"/>')
        self._adopt_limits(resp)

    def _adopt_limits(self, resp):
        try:
            root = ET.fromstring(resp)
        except ET.ParseError:
            return
        for key in ("MaxPayloadSizeToTargetInBytes",
                    "MaxPayloadSizeToTargetInBytesSupported"):
            v = root.get(key)
            if v and v.isdigit():
                self.max_payload = min(int(v), MAX_PAYLOAD)
                break
        v = root.get("SECTOR_SIZE_IN_BYTES")
        if v and v.isdigit():
            self.sector_size = int(v)
        self.log.info("firehose: payload=%d sector=%d storage=%s",
                      self.max_payload, self.sector_size, root.get("MemoryName", "?"))

    def get_storage_info(self, lun=0):
        resp = self.cmd(f'<getStorageInfo PhysicalPartitionNumber="{lun}" />',
                        allow_nak=True)
        try:
            return dict(ET.fromstring(resp).attrib)
        except ET.ParseError:
            return {}

    def _sectors(self, nbytes):
        s = self.sector_size
        return (nbytes + s - 1) // s

    def program(self, lun, start_sector, data, name="raw.bin"):
        total = self._sectors(len(data)) * self.sector_size
        padded = data.ljust(total, b"\x00")
        off = 0
        while off < total:
            room = self.max_payload - 512
            chunk = padded[off:off + room]
            chunk = chunk[:(len(chunk) // self.sector_size) * self.sector_size]
            if not chunk:
                break
            self.cmd(
                f'<program SECTOR_SIZE_IN_BYTES="{self.sector_size}" '
                f'num_partitions="1" physical_partition_number="{lun}" '
                f'start_sector="{start_sector + off // self.sector_size}" '
                f'filename="{name}" />', chunk)
            off += len(chunk)
            self.log.debug("firehose: programmed %d/%d", off, total)

    def read(self, lun, start_sector, nbytes, chunk=None):
        out = bytearray()
        want = self._sectors(nbytes) * self.sector_size
        cs = chunk or (self.max_payload - 512)
        cs = (cs // self.sector_size) * self.sector_size
        cur = start_sector
        while len(out) < want:
            n = min(cs, want - len(out))
            self.cmd(f'<read SECTOR_SIZE_IN_BYTES="{self.sector_size}" '
                     f'num_partitions="1" physical_partition_number="{lun}" '
                     f'start_sector="{cur}" NUM_SECTORS="{n // self.sector_size}" '
                     f'filename="raw.bin" />')
            out += self._drain(n)
            cur += n // self.sector_size
            self.log.debug("firehose: read %d/%d", len(out), want)
        return bytes(out[:nbytes])

    def _drain(self, n):
        buf = bytearray(self._rx)
        self._rx.clear()
        while len(buf) < n:
            c = self.t.read(min(65536, n - len(buf)), 5000)
            if not c:
                raise DeviceGone("firehose: payload stall")
            buf += c
        return bytes(buf[:n])

    def erase(self, lun, start_sector, nbytes):
        self.cmd(f'<erase SECTOR_SIZE_IN_BYTES="{self.sector_size}" '
                 f'num_partitions="1" physical_partition_number="{lun}" '
                 f'start_sector="{start_sector}" '
                 f'NUM_SECTORS="{self._sectors(nbytes)}" filename="" />')

    def patch(self, lun, byte_offset, value, size=1):
        self.cmd(f'<patch SECTOR_SIZE_IN_BYTES="{self.sector_size}" '
                 f'byte_offset="{byte_offset}" filename="DISK" '
                 f'physical_partition_number="{lun}" size_in_bytes="{size}" '
                 f'value="{value}" what="Update" start_sector="0" />')

    def power(self, mode="reset"):
        try:
            self.cmd(f'<power value="{mode}"/>', allow_nak=True)
        except (ProtocolError, DeviceGone):
            pass  # device drops the link mid-response
