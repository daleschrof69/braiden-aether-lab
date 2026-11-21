"""Serial transport for Samsung download mode (CDC ACM)."""
import serial

from ..errors import DeviceGone, TransportError
from .base import Transport

SAMSUNG_VID = 0x04E8
SAMSUNG_DOWNLOAD_PIDS = (0x685D, 0x685E)


class SerialTransport(Transport):
    def __init__(self, port, baud=115200, label="serial"):
        self.label = label
        try:
            self.ser = serial.Serial(port, baud, timeout=1, write_timeout=10)
        except serial.SerialException as e:
            raise TransportError(f"{label}: {e}") from e

    def read(self, n, timeout_ms=1000):
        self.ser.timeout = timeout_ms / 1000
        try:
            return self.ser.read(n)
        except serial.SerialException as e:
            raise DeviceGone(str(e)) from e

    def write(self, data, timeout_ms=5000):
        try:
            return self.ser.write(data) or len(data)
        except serial.SerialException as e:
            raise DeviceGone(str(e)) from e

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def find_download_mode():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == SAMSUNG_VID and p.pid in SAMSUNG_DOWNLOAD_PIDS:
            return SerialTransport(p.device, label="samsung-download")
    raise TransportError("no Samsung download-mode port")
