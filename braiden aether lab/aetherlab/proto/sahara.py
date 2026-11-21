"""Qualcomm Sahara - the EDL bootstrap protocol.

EDL is two stages. Sahara is stage one: the mask ROM speaks it over the vendor
bulk interface at 05c6:9008. Its only job is to accept a signed Firehose
programmer into RAM and jump to it. Everything after that is Firehose.

Framing is an 8-byte header - u32 command, u32 length - followed by exactly
`length` bytes, little-endian. No checksum: the ROM validates the image
signature itself, which is why an unsigned or wrong-SoC loader fails at
END_IMAGE_TX with status 0x05/0x07 rather than mid-transfer.

  hello        {u32 cmd, u32 len, u32 ver, u32 ver_min, u32 max_cmd,
                u32 mode, u32 rsvd[6]}                    48 bytes
  hello_resp   {...same 48..., u32 status}               52 bytes
  read_data    {u32 cmd, u32 len, u32 image_id, u32 off, u32 len}  20 bytes
  end_image_tx {u32 cmd, u32 len, u32 image_id, u32 status}        16 bytes
"""
import struct

from ..errors import AuthError, ProtocolError
from ..transport.base import Transport

HELLO = 0x01
HELLO_RESPONSE = 0x02
READ_DATA = 0x03
END_IMAGE_TX = 0x04
DONE = 0x05
DONE_RESPONSE = 0x06
RESET = 0x07
RESET_RESPONSE = 0x08
CMD_READY = 0x0B
CMD_SWITCH_MODE = 0x0C
CMD_EXEC = 0x0D
CMD_EXEC_RESPONSE = 0x0E
CMD_EXEC_DATA = 0x0F

MODE_IMAGE_TX = 0x00
MODE_COMMAND = 0x03

PROTOCOL_VERSION = 2
PROTOCOL_VERSION_MIN = 1
MAX_CMD_LEN = 4096

STATUS_OK = 0x00
STATUS_ERRORS = {
    0x01: "unsupported mode", 0x02: "invalid header", 0x03: "invalid image id",
    0x04: "hash mismatch", 0x05: "signature mismatch", 0x06: "invalid image size",
    0x07: "unexpected image id", 0x08: "invalid offset", 0x09: "image too large",
    0x0A: "unsupported command", 0x0B: "size limit exceeded",
    0x0C: "unexpected transfer",
}


class Hello:
    __slots__ = ("version", "version_min", "max_cmd_len", "mode", "chipset_id", "raw")

    def __init__(self, raw):
        (self.version, self.version_min, self.max_cmd_len,
         self.mode) = struct.unpack_from("<IIII", raw, 8)
        # 24..48 is reserved; several SoC families stuff a chipset serial there.
        self.chipset_id = struct.unpack_from("<I", raw, 24)[0] if len(raw) >= 28 else 0
        self.raw = raw

    def __repr__(self):
        return (f"Hello(ver={self.version}.{self.version_min} "
                f"max_cmd={self.max_cmd_len} mode={self.mode:#x} chip={self.chipset_id:#x})")


class Sahara:
    def __init__(self, t: Transport, log):
        self.t = t
        self.log = log
        self.hello_pkt = None

    def _send(self, cmd, body=b""):
        self.t.write(struct.pack("<II", cmd, len(body)) + body)

    def _recv(self):
        cmd, length = struct.unpack("<II", self.t.read_exact(8))
        if length > 0x200000:
            raise ProtocolError(f"sahara: absurd packet length {length}")
        return cmd, (self.t.read_exact(length) if length else b"")

    def handshake(self) -> Hello:
        cmd, body = self._recv()
        if cmd != HELLO:
            raise ProtocolError(f"sahara: expected HELLO, got {cmd:#x}")
        if len(body) != 40:
            raise ProtocolError(f"sahara: HELLO body {len(body)} != 40")
        hello = Hello(struct.pack("<II", cmd, len(body)) + body)
        if hello.version < PROTOCOL_VERSION_MIN:
            raise ProtocolError(f"sahara: device speaks {hello.version}")
        self.hello_pkt = hello
        self.log.info("sahara: %r", hello)
        self.t.write(struct.pack(
            "<IIIIII6II", HELLO_RESPONSE, 48,
            min(hello.version, PROTOCOL_VERSION), PROTOCOL_VERSION_MIN,
            hello.max_cmd_len or MAX_CMD_LEN, hello.mode,
            0, 0, 0, 0, 0, 0, STATUS_OK))
        if hello.mode != MODE_IMAGE_TX:
            self.log.info("sahara: device not in image mode (%#x)", hello.mode)
        return hello

    def send_image(self, image_id, data):
        total = len(data)
        self.log.info("sahara: serving image %d (%d bytes)", image_id, total)
        while True:
            cmd, body = self._recv()
            if cmd == READ_DATA:
                if len(body) != 12:
                    raise ProtocolError("sahara: bad READ_DATA")
                img, offset, length = struct.unpack("<III", body)
                if img != image_id:
                    raise ProtocolError(f"sahara: asked for {img}, serving {image_id}")
                if offset + length > total:
                    raise ProtocolError(f"sahara: read past EOF {offset}+{length}>{total}")
                self.t.write(data[offset:offset + length])
            elif cmd == END_IMAGE_TX:
                img, status = struct.unpack("<II", body)
                if status != STATUS_OK:
                    raise AuthError(
                        f"sahara: image {img} rejected: "
                        f"{STATUS_ERRORS.get(status, hex(status))} - wrong SoC or unsigned")
                self.log.info("sahara: image %d accepted", img)
            elif cmd == DONE:
                self._send(DONE_RESPONSE)
                self.log.info("sahara: transfer complete")
                return
            elif cmd == CMD_READY:
                self._send(CMD_READY)
            elif cmd == CMD_SWITCH_MODE:
                self._send(CMD_SWITCH_MODE, body[:4])
            else:
                raise ProtocolError(f"sahara: unexpected {cmd:#x} during image tx")

    def switch_to_command(self):
        self._send(CMD_SWITCH_MODE, struct.pack("<I", MODE_COMMAND))
        while True:
            cmd, body = self._recv()
            if cmd == CMD_READY:
                return
            if cmd == CMD_SWITCH_MODE:
                self._send(CMD_SWITCH_MODE, struct.pack("<I", MODE_COMMAND))
                return
            raise ProtocolError(f"sahara: unexpected {cmd:#x} switching mode")

    def exec_command(self, command):
        raw = command.encode() + b"\x00"
        self._send(CMD_EXEC, struct.pack("<I", len(raw)) + raw)
        cmd, body = self._recv()
        if cmd != CMD_EXEC_RESPONSE:
            raise ProtocolError(f"sahara: expected EXEC_RESPONSE, got {cmd:#x}")
        status = struct.unpack("<I", body[:4])[0]
        if status != STATUS_OK:
            raise ProtocolError(f"sahara: exec rejected ({status:#x})")
        out = bytearray()
        while True:
            cmd, body = self._recv()
            if cmd == CMD_EXEC_DATA:
                out += body
            elif cmd == CMD_EXEC_RESPONSE:
                return bytes(out)
            else:
                raise ProtocolError(f"sahara: bad exec stream {cmd:#x}")

    def reset(self):
        try:
            self._send(RESET)
            self._recv()
        except Exception:
            pass
