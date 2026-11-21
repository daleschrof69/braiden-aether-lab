"""FRP / setup-wizard reset, by access path.

`adb` is the free path and works on any device that reaches the setup wizard
with USB debugging live. It is three state writes, not a bypass: it marks setup
complete and clears the GMS cache so the wizard does not restart. Anything that
also requires the account password still will.

`edl` and `mtk` erase the FRP flag at the storage layer, which is what the
commercial tools do and what survives a factory reset.
"""
import shutil
import subprocess

from ..errors import AetherError


def _adb(serial, *args, timeout=25):
    exe = shutil.which("adb")
    if not exe:
        raise AetherError("adb not on PATH")
    cmd = [exe] + (["-s", serial] if serial else []) + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def adb_state(serial=None):
    rc, out, _ = _adb(serial, "get-state")
    return out if rc == 0 else "absent"


def adb_reset(serial, log):
    """Clear FRP state through the wizard. Returns the steps that applied."""
    steps = []
    plan = [
        ("settings put secure user_setup_complete 1", "setup flag"),
        ("settings put global device_provisioned 1", "provisioned flag"),
        ("pm clear com.google.android.gms", "gms cache"),
        ("pm clear com.google.android.gsf", "gsf cache"),
        ("am start -n com.google.android.gms/.auth.uiflows.minutemaid.MinuteMaidActivity",
         "wizard"),
    ]
    for cmd, label in plan:
        rc, _, err = _adb(serial, "shell", cmd)
        if rc == 0:
            steps.append(label)
            log.info("frp: %s", label)
        else:
            log.warning("frp: %s failed: %s", label, err)
    return steps


def adb_reboot(serial, target="", log=None):
    _adb(serial, "reboot", *([target] if target else []))
    if log:
        log.info("frp: rebooting %s", target or "system")


def erase_edl(log, chipset=None, image_id=None, memory_name="ufs"):
    from .edl import EdlSession
    with EdlSession(log, chipset, image_id, memory_name=memory_name) as s:
        return s.frp_erase()


def erase_mtk(log, soc):
    from .mtk import MtkSession
    with MtkSession(log, soc) as s:
        return s.frp_erase()
