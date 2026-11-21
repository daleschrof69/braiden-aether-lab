"""Transport contract. Everything above this line is protocol-agnostic."""
import abc

from ..errors import DeviceGone


class Transport(abc.ABC):
    """Byte pipe to a device.

    Implementations must guarantee: read() returns at most n bytes and blocks up
    to timeout; write() sends everything or raises; close() is idempotent.
    """

    label = "transport"

    @abc.abstractmethod
    def read(self, n: int, timeout_ms: int = 1000) -> bytes: ...

    @abc.abstractmethod
    def write(self, data: bytes, timeout_ms: int = 5000) -> int: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    def read_exact(self, n: int, timeout_ms: int = 1000) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.read(n - len(buf), timeout_ms)
            if not chunk:
                raise DeviceGone(f"{self.label}: short read at {len(buf)}/{n}")
            buf += chunk
        return bytes(buf)

    def write_str(self, s: str) -> int:
        return self.write(s.encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
