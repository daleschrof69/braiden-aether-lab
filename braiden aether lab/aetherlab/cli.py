"""Command line interface. Every subcommand maps to an ops function."""
import argparse
import json
import sys

from . import BANNER, __version__, config, log as alog
from .errors import AetherError


def build_parser():
    p = argparse.ArgumentParser(prog="aether", description="Braiden's Aether Lab")
    p.add_argument("-d", "--debug", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="list attached devices")

    e = sub.add_parser("edl", help="Qualcomm EDL operations")
    e.add_argument("--chipset")
    e.add_argument("--image-id", type=lambda s: int(s, 0))
    e.add_argument("--sector-size", type=int)
    e.add_argument("--memory", default="ufs", choices=["ufs", "emmc"])
    es = e.add_subparsers(dest="op", required=True)
    es.add_parser("info")
    d = es.add_parser("dump"); d.add_argument("part"); d.add_argument("out")
    d.add_argument("--lun", type=int)
    w = es.add_parser("write"); w.add_argument("part"); w.add_argument("src")
    w.add_argument("--lun", type=int); w.add_argument("--force", action="store_true")
    r = es.add_parser("erase"); r.add_argument("part"); r.add_argument("--lun", type=int)
    es.add_parser("frp")
    es.add_parser("unlock-bl")
    f = es.add_parser("flash")
    f.add_argument("--rawprogram", required=True)
    f.add_argument("--dir", required=True)
    f.add_argument("--patch")
    b = es.add_parser("backup-all"); b.add_argument("out")
    b.add_argument("--with-userdata", action="store_true")

    m = sub.add_parser("mtk", help="MediaTek operations")
    m.add_argument("--soc", required=True)
    m.add_argument("--da-addr", type=lambda s: int(s, 0))
    ms = m.add_subparsers(dest="op", required=True)
    ms.add_parser("info")
    md = ms.add_parser("dump"); md.add_argument("part"); md.add_argument("out")
    mw = ms.add_parser("write"); mw.add_argument("part"); mw.add_argument("src")
    mw.add_argument("--force", action="store_true")
    me = ms.add_parser("erase"); me.add_argument("part")
    ms.add_parser("format")
    ms.add_parser("frp")
    ms.add_parser("unlock")
    mn = ms.add_parser("nvram"); mn.add_argument("--read"); mn.add_argument("--write")

    o = sub.add_parser("odin", help="Samsung download mode")
    o.add_argument("--model", required=True)
    os_ = o.add_subparsers(dest="op", required=True)
    os_.add_parser("pit")
    of = os_.add_parser("flash"); of.add_argument("tars", nargs="+")
    os_.add_parser("reboot")

    fr = sub.add_parser("frp", help="FRP reset")
    fs = fr.add_subparsers(dest="op", required=True)
    fa = fs.add_parser("adb"); fa.add_argument("--serial", required=True)
    fa.add_argument("--reboot", action="store_true")
    fe = fs.add_parser("erase"); fe.add_argument("--chipset")
    fe.add_argument("--memory", default="ufs")
    fm = fs.add_parser("mtk"); fm.add_argument("--soc", required=True)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    log = alog.get(debug=args.debug, path=str(config.LOGS / "session.log"))
    if not args.json:
        print(BANNER)
    log.info("aether %s %s", __version__, args.cmd)

    try:
        rc = dispatch(args, log)
    except AetherError as e:
        log.error("%s", e)
        return 2
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    return rc or 0


def _out(args, obj):
    if args.json:
        print(json.dumps(obj, indent=2, default=str))
    return obj


def dispatch(args, log):
    from .transport.usb import enumerate_devices

    if args.cmd == "devices":
        return _out(args, enumerate_devices())

    if args.cmd == "frp":
        from .ops import frp
        if args.op == "adb":
            steps = frp.adb_reset(args.serial, log)
            if args.reboot:
                frp.adb_reboot(args.serial, log=log)
            return _out(args, {"steps": steps})
        if args.op == "erase":
            return _out(args, {"erased": frp.erase_edl(log, args.chipset,
                                                       memory_name=args.memory)})
        if args.op == "mtk":
            return _out(args, {"erased": frp.erase_mtk(log, args.soc)})

    if args.cmd == "edl":
        from .ops.edl import EdlSession
        with EdlSession(log, args.chipset, args.image_id,
                        args.sector_size, args.memory) as s:
            if args.op == "info":
                info = {"partitions": [
                    {"lun": lun, "name": e.name, "sectors": e.sectors}
                    for lun, e in s.list_partitions()]}
                if not args.json:
                    for p in info["partitions"]:
                        print(f"lun{p['lun']:<2} {p['name']:<28} {p['sectors']:>10}")
                return _out(args, info)
            if args.op == "dump":
                return _out(args, {"out": s.dump(args.part, args.out, lun=args.lun)})
            if args.op == "write":
                s.write(args.part, args.src, lun=args.lun, force=args.force)
                return _out(args, {"ok": True})
            if args.op == "erase":
                s.erase(args.part, lun=args.lun)
                return _out(args, {"ok": True})
            if args.op == "frp":
                return _out(args, {"erased": s.frp_erase()})
            if args.op == "unlock-bl":
                return _out(args, {"changed": s.unlock_bootloader()})
            if args.op == "flash":
                s.flash_rawprogram(args.rawprogram, args.dir, args.patch)
                return _out(args, {"ok": True})
            if args.op == "backup-all":
                from .ops import backup
                return _out(args, {"manifest": backup.backup(
                    s, args.out, args.with_userdata, log)})

    if args.cmd == "mtk":
        from .ops.mtk import MtkSession
        with MtkSession(log, args.soc, args.da_addr) as s:
            if args.op == "info":
                return _out(args, s.info())
            if args.op == "dump":
                return _out(args, {"out": s.dump(args.part, args.out)})
            if args.op == "write":
                s.write(args.part, args.src, force=args.force)
                return _out(args, {"ok": True})
            if args.op == "erase":
                s.erase(args.part)
                return _out(args, {"ok": True})
            if args.op == "format":
                return _out(args, {"erased": s.frp_erase()})
            if args.op == "frp":
                return _out(args, {"erased": s.frp_erase()})
            if args.op == "unlock":
                return _out(args, {"changed": s.unlock_network()})
            if args.op == "nvram":
                if args.read:
                    s.read_nvram(args.read)
                    return _out(args, {"dir": args.read})
                if args.write:
                    s.write_nvram(args.write)
                    return _out(args, {"ok": True})

    if args.cmd == "odin":
        from .ops import odin_ops
        if args.op == "pit":
            return _out(args, {"pit": odin_ops.read_pit(log, args.model)})
        if args.op == "flash":
            return _out(args, odin_ops.flash(log, args.model, args.tars))
        if args.op == "reboot":
            return _out(args, odin_ops.reboot(log, args.model))

    return 0


if __name__ == "__main__":
    sys.exit(main())
