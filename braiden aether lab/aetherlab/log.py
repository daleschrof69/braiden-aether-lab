"""Timestamped logging plus a ring buffer the GUI polls."""
import collections
import logging
import sys
import threading

RING = collections.deque(maxlen=4000)
_LOCK = threading.Lock()


class RingHandler(logging.Handler):
    def emit(self, record):
        with _LOCK:
            RING.append(self.format(record))


class _Fmt(logging.Formatter):
    LEVELS = {"DEBUG": "DBG", "INFO": "+", "WARNING": "!",
              "ERROR": "X", "CRITICAL": "XX"}

    def format(self, record):
        tag = self.LEVELS.get(record.levelname, record.levelname[:3])
        return f"{self.formatTime(record, '%H:%M:%S')} [{tag}] {record.getMessage()}"


def get(name="aether", debug=False, path=None):
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = _Fmt()
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    rh = RingHandler()
    rh.setFormatter(fmt)
    log.addHandler(rh)
    if path:
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    log.propagate = False
    return log


def tail(n=200):
    with _LOCK:
        return list(RING)[-n:]
