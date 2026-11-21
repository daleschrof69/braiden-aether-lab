"""Whole-device backup and restore, plus a manifest that makes restore safe.

Manifest is JSON: device identity, per-partition SHA-256, and the source GPT so
restore can rebuild the table before writing payloads. A restore without the
GPT is how people end up with a phone that has the right bytes at the wrong
offsets.
"""
import hashlib
import json
import os
import time

from ..errors import PartitionError
from . import gpt as gptmod

SKIP = {"userdata", "super", "cache", "metadata", "misc", "frp"}


def _manifest_path(dest):
    return os.path.join(dest, "manifest.json")


def backup(session, dest, include_userdata=False, log=None):
    os.makedirs(dest, exist_ok=True)
    man = {"created": time.time(), "sector_size": session.fh.sector_size,
           "partitions": [], "gpts": {}}
    for lun in range(6):
        try:
            g = session.gpt(lun)
        except PartitionError:
            continue
        man["gpts"][lun] = g.disk_guid
        for e in g.entries:
            if not include_userdata and e.base_name in SKIP:
                continue
            name = e.name
            path = os.path.join(dest, f"lun{lun}_{name}.img")
            if log:
                log.info("backup: %s", name)
            session.dump(name, path, lun=lun)
            man["partitions"].append({
                "lun": lun, "name": name,
                "first_lba": e.first_lba, "sectors": e.sectors,
                "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
            })
    with open(_manifest_path(dest), "w") as f:
        json.dump(man, f, indent=2)
    return _manifest_path(dest)


def restore(session, src, log=None):
    man = json.load(open(_manifest_path(src)))
    written = 0
    for rec in man["partitions"]:
        path = os.path.join(src, f"lun{rec['lun']}_{rec['name']}.img")
        if not os.path.isfile(path):
            if log:
                log.warning("restore: missing %s", path)
            continue
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if got != rec["sha256"]:
            if log:
                log.warning("restore: %s sha256 differs, writing anyway", rec["name"])
        if log:
            log.info("restore: %s", rec["name"])
        session.write_raw(rec["lun"], rec["first_lba"], path)
        written += 1
    return written


def rebuild_gpt(session, src, log=None):
    """Rewrite a stored GPT verbatim. Use before restore on a blank board."""
    man = json.load(open(_manifest_path(src)))
    for lun_s, guid in man["gpts"].items():
        lun = int(lun_s)
        g = gptmod.Gpt(man["sector_size"], lun)
        g.disk_guid = guid
        for rec in man["partitions"]:
            if rec["lun"] == lun:
                g.add(rec["name"], rec["first_lba"],
                      rec["first_lba"] + rec["sectors"] - 1)
        g.last_usable = max(e.last_lba for e in g.entries)
        front, (backup_hdr, table) = gptmod.build(g)
        if log:
            log.info("gpt: rewriting lun %d", lun)
        session.fh.program(lun, 0, gptmod.pmbr() + front)
        session.fh.program(lun, g.last_usable + 34, backup_hdr + table)
