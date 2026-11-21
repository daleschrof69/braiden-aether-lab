"""USB transport over libusb.

EDL and MTK BROM both expose one vendor-specific interface with a bulk IN and
bulk OUT pair. We discover the endpoint addresses instead of hardcoding them
because they differ per SoC and per host controller.
"""
import time

import usb.core
import usb.util

from ..errors import DeviceGone, TransportError
from .base import Transport

QUALCOMM_EDL = (0x05C6, 0x9008, "qualcomm-edl")
QUALCOMM_EDL_ALT = (0x05C6, 0x900E, "qualcomm-edl-900e")
MTK_BROM = (0x0E8D, 0x0003, "mtk-brom")
MTK_PRELOADER = (0x0E8D, 0x2000, "mtk-preloader")
MTK_DA = (0x0E8D, 0x2001, "mtk-da")

ALL = (QUALCOMM_EDL, QUALCOMM_EDL_ALT, MTK_BROM, MTK_PRELOADER, MTK_DA)


class USBTransport(Transport):
    def __init__(self, dev, ep_in, ep_out, label, timeout_ms=5000):
        self.dev = dev
        self.ep_in = ep_in
        self.ep_out = ep_out
        self.label = label
        self.timeout_ms = timeout_ms

    def read(self, n, timeout_ms=None):
        try:
            return bytes(self.dev.read(self.ep_in, n, timeout_ms or self.timeout_ms))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as e:
            if e.errno in (5, 19):
                raise DeviceGone(f"{self.label}: {e}") from e
            raise TransportError(f"{self.label}: {e}") from e

    def write(self, data, timeout_ms=None):
        try:
            return self.dev.write(self.ep_out, data, timeout_ms or self.timeout_ms)
        except usb.core.USBError as e:
            if e.errno in (5, 19):
                raise DeviceGone(f"{self.label}: {e}") from e
            raise TransportError(f"{self.label}: {e}") from e

    def close(self):
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass

    @classmethod
    def open(cls, vid, pid, label, attempts=20):
        dev = None
        for _ in range(attempts):
            dev = usb.core.find(idVendor=vid, idProduct=pid)
            if dev:
                break
            time.sleep(0.5)
        if not dev:
            raise TransportError(f"{label}: not found ({vid:04x}:{pid:04x})")
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass
        iface, ep_in, ep_out = _find_bulk(dev)
        try:
            usb.util.claim_interface(dev, iface)
        except usb.core.USBError as e:
            raise TransportError(f"{label}: claim failed: {e}") from e
        return cls(dev, ep_in, ep_out, label)


def _find_bulk(dev):
    for cfg in dev:
        for intf in cfg:
            ep_in = ep_out = None
            for ep in intf:
                d = usb.util.endpoint_direction(ep.bEndpointAddress)
                if d == usb.util.ENDPOINT_IN and ep_in is None:
                    ep_in = ep.bEndpointAddress
                elif d == usb.util.ENDPOINT_OUT and ep_out is None:
                    ep_out = ep.bEndpointAddress
            if ep_in and ep_out:
                return intf.bInterfaceNumber, ep_in, ep_out
    raise TransportError("no bulk endpoint pair on any interface")


def find_edl():
    for vid, pid, label in (QUALCOMM_EDL, QUALCOMM_EDL_ALT):
        if usb.core.find(idVendor=vid, idProduct=pid):
            return USBTransport.open(vid, pid, label)
    raise TransportError("no Qualcomm EDL device present")


def find_mtk():
    for vid, pid, label in (MTK_BROM, MTK_PRELOADER, MTK_DA):
        if usb.core.find(idVendor=vid, idProduct=pid):
            return USBTransport.open(vid, pid, label)
    raise TransportError("no MediaTek BROM/preloader device present")


def enumerate_devices():
    out = []
    for vid, pid, label in ALL:
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev:
            try:
                serial = usb.util.get_string(dev, dev.iSerialNumber) or ""
            except Exception:
                serial = ""
            out.append({"label": label, "vid": vid, "pid": pid,
                        "bus": dev.bus, "addr": dev.address, "serial": serial})
    return out
