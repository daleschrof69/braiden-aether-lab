"""Odin session wrapper: handshake, LOKE, PIT, file pushes."""
import os

from .. import config
from ..errors import AetherError, PartitionError
from ..proto.odin import Odin
from ..transport.serial import find_download_mode
from . import firmware


class PitTable:
    def __init__(self, raw):
        import struct
        self.entries = {}
        if raw[:4] != b"\x00\x00\x00\x00" and len(raw) < 32:
            raise PartitionError("PIT too short")
        for i in range(0, len(raw) - 28, 28):
            rec = raw[i:i + 28]
            pid, _ = struct.unpack_from("<IH", rec, 0)
            if pid == 0:
                continue
            name = rec[12:28].decode("utf-16-le", "replace").split("\x00")[0]
            if name:
                self.entries[name] = pid

    def id(self, name):
        if name in self.entries:
            return self.entries[name]
        raise PartitionError(f"{name} not in PIT")


def _session(log, model):
    t = find_download_mode()
    o = Odin(t, log, model)
    o.begin()
    loke = config.find_loke(model).read_bytes()
    o.send_loke(loke)
    return o, t


def read_pit(log, model):
    o, t = _session(log, model)
    try:
        return o.read_pit().hex()
    finally:
        t.close()


def flash(log, model, tars):
    o, t = _session(log, model)
    pit = PitTable(o.read_pit())
    written = []
    try:
        for path in tars:
            if path.endswith(".md5"):
                log.info("odin: verifying %s", os.path.basename(path))
                firmware.verify_md5(path)
            slot = firmware.classify(path)
            if slot in ("CSC", "HOME_CSC"):
                continue  # CSC carries PIT + config, not payload partitions
            for m, fh in firmware.members(path):
                base = os.path.basename(m.name)
                stem = base.split(".")[0]
                if stem not in pit.entries:
                    log.warning("odin: %s not in PIT, skipping", stem)
                    continue
                data = fh.read()
                o.flash(pit.id(stem), data, base)
                written.append(base)
        o.end()
    finally:
        t.close()
    return {"written": written}


def reboot(log, model):
    o, t = _session(log, model)
    try:
        o.reboot()
    finally:
        t.close()
    return {"ok": True}
