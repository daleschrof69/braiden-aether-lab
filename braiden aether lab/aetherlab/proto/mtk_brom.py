"""MediaTek BROM / Preloader handshake and DA upload.

BROM handshake, bulk pipe, all bytes literal:

  host -> A0          device -> 5F        (alive)
  host -> 0A          device -> F5        (checksum = ~0x0A)
  host -> 50          device -> AF        (checksum = ~0x50)

DA upload (BROM only):

  host -> A0          device -> 5F
  host -> u32 size BE device -> 5F        (size accepted)
  host -> chunk       device -> 5F each   (chunk = data + u16 byte-sum BE)
  host -> u32 total BE                    device -> C0 ok / C1 bad sum

Jump:

  host -> u32 da_addr BE, D5

Checksums are plain byte sums, not CRC. The preloader handshake is identical to
BROM except the alive byte pair differs on 2020+ parts and the ROM pushes a
version string after ALIVE - if handshake() times out on a modern SoC, that is
why, not your cable.
"""
import struct

from ..errors import AuthError, ProtocolError
from ..transport.base import Transport

HANDSHAKE_BYTE = 0xA0
ALIVE = 0x5F
ACCEPT = 0xC0
BAD_SUM = 0xC1
CMD_JUMP_DA = 0xD5
CHUNK = 0x1000


def sum16(data):
    return sum(data) & 0xFFFF


def sum32(data):
    return sum(data) & 0xFFFFFFFF


class Brom:
    def __init__(self, t: Transport, log):
        self.t = t
        self.log = log

    def _read1(self, timeout=2000):
        b = self.t.read(1, timeout)
        if not b:
            raise ProtocolError("brom: device silent")
        return b[0]

    def _expect(self, want, what):
        got = self._read1()
        if got != want:
            raise ProtocolError(f"brom: {what}: got {got:#04x} want {want:#04x}")

    def handshake(self, retries=8):
        for _ in range(retries):
            self.t.write(bytes([HANDSHAKE_BYTE]))
            if self._read1(500) == ALIVE:
                break
        else:
            raise ProtocolError("brom: no ALIVE - wrong driver, wrong mode, or "
                                "preloader-only device")
        self.t.write(b"\x0a")
        self._expect(0xF5, "echo 0x0a")
        self.t.write(b"\x50")
        self._expect(0xAF, "echo 0x50")
        self.log.info("brom: handshake ok")

    def send_da(self, da, chunk=CHUNK):
        self.t.write(bytes([HANDSHAKE_BYTE]))
        self._expect(ALIVE, "send_da alive")
        self.t.write(struct.pack(">I", len(da)))
        self._expect(ALIVE, "send_da size")
        for off in range(0, len(da), chunk):
            piece = da[off:off + chunk]
            self.t.write(piece + struct.pack(">H", sum16(piece)))
            self._expect(ALIVE, f"send_da chunk @{off}")
            self.log.debug("brom: da %d/%d", off + len(piece), len(da))
        self.t.write(struct.pack(">I", sum32(da)))
        got = self._read1(5000)
        if got == BAD_SUM:
            raise AuthError("brom: DA rejected - checksum mismatch")
        if got != ACCEPT:
            raise ProtocolError(f"brom: DA refused ({got:#04x}) - likely signed "
                                f"DA on a secure-boot part")
        self.log.info("brom: DA accepted")

    def jump_da(self, addr):
        self.t.write(struct.pack(">I", addr) + bytes([CMD_JUMP_DA]))
        self.log.info("brom: jumped to DA @ %#x", addr)
