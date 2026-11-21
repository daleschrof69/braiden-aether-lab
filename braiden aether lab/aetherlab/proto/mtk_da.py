"""MediaTek DA (Download Agent) command protocol, post-jump.

Framing per command, all big-endian:

  host -> cmd byte          device -> same cmd byte (echo)
  host -> u32 addr          device -> 4 echo bytes
  host -> u32 length        device -> 4 echo bytes
  host -> data + u16 sum + u32 sum      (writes)
  device -> data + u16 sum + u32 sum    (reads)
  trailing party sends 2 status bytes

Checksums are plain byte sums. A status of 0x00 is success; anything else means
the DA rejected the range (unmapped, protected, or wrong length).
"""
import struct

from ..errors import ProtocolError
from ..transport.base import Transport

CMD_READ16 = 0xD0
CMD_READ32 = 0xD1
CMD_WRITE16 = 0xD2
CMD_WRITE32 = 0xD4
CMD_JUMP_BL = 0xD5
CMD_SEND_PARTITION_DATA = 0xD6
CMD_GET_TARGET_CONFIG = 0xD8
CMD_GET_TARGET_SVN = 0xC9
CMD_GET_VERSION = 0xE7
CMD_GET_BL_VER = 0xEF
CMD_GET_HW_SW_VER = 0xFC
CMD_GET_HW_CODE = 0xFD
CMD_GET_BROM_VERSION = 0xFE

STATUS_OK = 0x0000


def sum16(d):
    return sum(d) & 0xFFFF


def sum32(d):
    return sum(d) & 0xFFFFFFFF


class MtkDa:
    def __init__(self, t: Transport, log):
        self.t = t
        self.log = log

    def _echo(self, cmd):
        self.t.write(bytes([cmd]))
        got = self.t.read_exact(1)
        if got[0] != cmd:
            raise ProtocolError(f"da: echo {got[0]:#04x} != {cmd:#04x}")

    def _echo_field(self, value):
        raw = struct.pack(">I", value)
        self.t.write(raw)
        got = self.t.read_exact(4)
        if got != raw:
            raise ProtocolError(f"da: field echo mismatch {got.hex()}")

    def _status(self):
        st = struct.unpack(">H", self.t.read_exact(2))[0]
        if st != STATUS_OK:
            raise ProtocolError(f"da: status {st:#06x}")
        return st

    def _write(self, cmd, addr, data):
        self._echo(cmd)
        self._echo_field(addr)
        self._echo_field(len(data))
        self.t.write(data + struct.pack(">H", sum16(data))
                     + struct.pack(">I", sum32(data)))
        self._status()

    def _read(self, cmd, addr, length):
        self._echo(cmd)
        self._echo_field(addr)
        self._echo_field(length)
        data = self.t.read_exact(length)
        if sum16(data) != struct.unpack(">H", self.t.read_exact(2))[0]:
            raise ProtocolError("da: read checksum mismatch")
        if sum32(data) != struct.unpack(">I", self.t.read_exact(4))[0]:
            raise ProtocolError("da: read total checksum mismatch")
        self.t.write(b"\x00\x00")
        return data

    def read32(self, addr, length):
        return self._read(CMD_READ32, addr, length)

    def write32(self, addr, data):
        self._write(CMD_WRITE32, addr, data)

    def read16(self, addr, length):
        return self._read(CMD_READ16, addr, length)

    def write16(self, addr, data):
        self._write(CMD_WRITE16, addr, data)

    def _simple(self, cmd, length=0):
        self._echo(cmd)
        data = self.t.read_exact(length) if length else b""
        self._status()
        return data

    def hw_code(self):
        return struct.unpack(">H", self._simple(CMD_GET_HW_CODE, 2))[0]

    def hw_sw_ver(self):
        return self._simple(CMD_GET_HW_SW_VER, 8)

    def bl_ver(self):
        return self._simple(CMD_GET_BL_VER, 8)

    def brom_version(self):
        return self._simple(CMD_GET_BROM_VERSION, 1)[0]

    def target_config(self):
        return self._simple(CMD_GET_TARGET_CONFIG, 4)

    def jump_bl(self, addr):
        self._echo(CMD_JUMP_BL)
        self._echo_field(addr)
        self.log.info("da: jumped to bootloader @ %#x", addr)
