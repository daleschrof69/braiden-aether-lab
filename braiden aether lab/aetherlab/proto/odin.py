"""Samsung Odin download-mode protocol.

Every packet is a 1024-byte header followed by the payload:

  u32 magic ("ODIN" 0x4f44494e)   u32 command id
  u32 payload size                u32 zero, u32 zero
  u32 payload checksum            u32 param
  ... zero pad to 1024 ...

Checksum is a 32-bit sum of the payload's little-endian u32 words. Responses
use magic "ODRN" (0x4f44524e) or "ODRP" (0x4f445250) with a u32 status at
param offset; 0 is success.

LOKE is required from bootloader 2 onward: the device will not accept a PIT or
a FILE until a signed LOKE blob matching the bootloader version is sent. That
blob is per-model and is the one artifact you cannot generate.
"""
import struct

from ..errors import AuthError, ProtocolError
from ..transport.base import Transport

MAGIC = 0x4F44494E
RESP_MAGIC = 0x4F44524E
RESP_MAGIC_P = 0x4F445250
HEADER = 1024

CMD_LOKE = 0x64
CMD_PIT = 0x65
CMD_FILE = 0x66
CMD_END = 0x67
CMD_INIT = 0x68
CMD_ODIN = 0x70

STATUS_OK = 0


def _checksum(payload):
    pad = (-len(payload)) % 4
    padded = payload + b"\x00" * pad
    return sum(struct.unpack(f"<{len(padded) // 4}I", padded)) & 0xFFFFFFFF


class Odin:
    def __init__(self, t: Transport, log, model="unknown"):
        self.t = t
        self.log = log
        self.model = model

    def _header(self, cmd, size, csum, param=0):
        raw = struct.pack("<IIIIIIII", MAGIC, cmd, size, 0, 0, csum, param, 0)
        return raw.ljust(HEADER, b"\x00")

    def send(self, cmd, payload=b"", param=0):
        self.t.write(self._header(cmd, len(payload), _checksum(payload), param))
        if payload:
            self.t.write(payload)

    def recv(self):
        hdr = self.t.read_exact(HEADER)
        magic, cmd, size = struct.unpack_from("<III", hdr, 0)
        if magic not in (RESP_MAGIC, RESP_MAGIC_P):
            raise ProtocolError(f"odin: bad response magic {magic:#x}")
        body = self.t.read_exact(size) if size else b""
        return cmd, body

    def begin(self) -> str:
        """Handshake. Returns the device bootloader string."""
        self.send(CMD_ODIN, b"ODIN")
        cmd, body = self.recv()
        if cmd != CMD_ODIN:
            raise ProtocolError(f"odin: handshake cmd {cmd:#x}")
        bl = body.decode("utf-8", "replace").strip("\x00 ")
        self.log.info("odin: device bootloader %s", bl or "?")
        return bl

    def send_loke(self, loke: bytes):
        if not loke:
            raise AuthError("odin: empty LOKE")
        self.send(CMD_LOKE, loke)
        cmd, body = self.recv()
        status = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else -1
        if status != STATUS_OK:
            raise AuthError(f"odin: LOKE rejected ({status}) - wrong version for "
                            f"this bootloader")
        self.log.info("odin: LOKE accepted")

    def read_pit(self) -> bytes:
        self.send(CMD_PIT)
        cmd, body = self.recv()
        if cmd != CMD_PIT:
            raise ProtocolError(f"odin: pit cmd {cmd:#x}")
        self.log.info("odin: PIT %d bytes", len(body))
        return body

    def write_pit(self, pit: bytes):
        self.send(CMD_PIT, pit, param=0)
        cmd, body = self.recv()
        status = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else -1
        if status != STATUS_OK:
            raise ProtocolError(f"odin: PIT write refused ({status})")
        self.log.info("odin: PIT written")

    def flash(self, partition_id: int, data: bytes, name="part.bin"):
        self.log.info("odin: %s -> pit id %d (%d bytes)", name, partition_id, len(data))
        self.send(CMD_FILE, data, param=partition_id)
        cmd, body = self.recv()
        status = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else -1
        if status != STATUS_OK:
            raise ProtocolError(f"odin: {name} refused ({status})")
        self.log.info("odin: %s written", name)

    def end(self):
        self.send(CMD_END)
        self.recv()

    def reboot(self, param=0):
        self.send(CMD_END, param=param)
        try:
            self.recv()
        except Exception:
            pass
