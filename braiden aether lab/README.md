# Braiden's Aether Lab

Free, source-available reimplementation of the Octoplus Premium feature set.
Personal use. MIT.

Octoplus is a closed-source Windows box tool. Its value is (a) a UI, (b) bundled
loaders/DA files, (c) server-side unlock. (a) and the protocol work are
reproducible. This repo does that and nothing else.

## Feature parity map

| Octoplus Premium menu            | Aether Lab replacement                        | Local? |
|----------------------------------|-----------------------------------------------|--------|
| Qualcomm > Flash firmware        | `aether edl flash --rawprogram x.xml`         | yes*   |
| Qualcomm > Read/Write partition  | `aether edl dump|write <part>`                | yes*   |
| Qualcomm > Erase FRP             | `aether edl frp`                              | yes    |
| Qualcomm > Full backup           | `aether edl backup-all`                       | yes*   |
| Qualcomm > Bootloader unlock     | `aether edl unlock-bl`                        | part** |
| MTK > Flash / Format / FRP       | `aether mtk flash|format|frp`                 | yes*   |
| MTK > Read/Write flash           | `aether mtk dump|write`                       | yes*   |
| MTK > IMEI repair                | `aether mtk imei --nvram nvdata.bin`          | part** |
| Samsung > Odin flash             | `aether odin flash BL.tar.md5 AP.tar.md5 ...` | yes*** |
| Samsung > PIT read/write         | `aether odin pit --read` / `--write x.pit`    | yes    |
| Samsung > FRP / MTP unlock       | `aether frp adb` / `aether frp erase`         | yes    |
| Huawei/ZTE/Alcatel FRP           | `aether frp adb` (generic ADB path)           | yes    |
| Network unlock (SIM)             | `aether mtk unlock --nc`                      | part** |

\* needs a loader/DA for the SoC (you supply)
\*\* needs device-specific data or a keygen service
\*\*\* needs a signed LOKE matching the bootloader

## Install

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt

Windows: install WinUSB/libusb for `05c6:9008` (Qualcomm EDL), `0e8d:0003`
(MTK BROM), `0e8d:2000` (MTK Preloader) with Zadig. Samsung `04e8:685d` uses
the Samsung CDC driver, no Zadig.

## Loader layout

    ~/.aetherlab/loaders/<chipset>/prog_firehose_ddr.elf
    ~/.aetherlab/loaders/<soc>/da.bin
    ~/.aetherlab/loke/<model>/loke3.pem

`aether devices` prints the chipset string it detects. That is the directory
name to use.

## Usage

    aether devices
    aether edl info
    aether edl dump super super.img
    aether edl frp
    aether mtk frp --soc mt6789
    aether odin flash BL_*.tar.md5 AP_*.tar.md5
    aether frp adb --serial R58M12345
    python run.py          # GUI

## Status

Protocol layer complete. Partition/GPT layer complete. Firmware packaging
complete. Per-model quirks thin - that is the long tail and it is where your
mileage will vary. Read the module docstrings before trusting a write.
