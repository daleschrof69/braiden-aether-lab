"""Paths and per-chipset loader resolution."""
import os
from pathlib import Path

from .errors import LoaderError

ROOT = Path(os.environ.get("AETHERLAB_HOME", Path.home() / ".aetherlab"))
LOADERS = ROOT / "loaders"
LOKE = ROOT / "loke"
FIRMWARE = ROOT / "firmware"
DUMPS = ROOT / "dumps"
LOGS = ROOT / "logs"

FIREHOSE_NAMES = ("prog_firehose_ddr.elf", "prog_firehose_lite.elf", "prog_firehose.elf")
DA_NAMES = ("da.bin", "MTK_AllInOne_DA.bin")

# Firehose image id the ROM expects. 13 covers SDM/QRB/SM 6xx-8xx; older 8x96
# wants 33 and 8x74 wants 5. Override with --image-id when a loader is refused.
DEFAULT_IMAGE_ID = 13

QUALCOMM_FRP_PARTITIONS = ("frp", "persist", "misc", "metadata")
MTK_FRP_PARTITIONS = ("frp", "protect_f")


def ensure_dirs():
    for d in (LOADERS, LOKE, FIRMWARE, DUMPS, LOGS):
        d.mkdir(parents=True, exist_ok=True)


def find_firehose(chipset) -> Path:
    d = LOADERS / str(chipset)
    for name in FIREHOSE_NAMES:
        p = d / name
        if p.is_file():
            return p
    raise LoaderError(
        f"no Firehose loader for chipset {chipset}. "
        f"Drop prog_firehose_ddr.elf into {d}/"
    )


def find_da(soc) -> Path:
    d = LOADERS / str(soc)
    for name in DA_NAMES:
        p = d / name
        if p.is_file():
            return p
    raise LoaderError(f"no DA for SoC {soc}. Drop da.bin into {d}/")


def find_loke(model) -> Path:
    p = LOKE / model / "loke3.pem"
    if p.is_file():
        return p
    raise LoaderError(f"no LOKE for {model} in {p.parent}/")
