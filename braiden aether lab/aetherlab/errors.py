class AetherError(Exception):
    """Base."""


class TransportError(AetherError):
    """USB/serial layer failed - cable, driver, or device dropped."""


class ProtocolError(AetherError):
    """Peer spoke something unexpected. Usually wrong mode or wrong SoC."""


class LoaderError(AetherError):
    """Missing or invalid loader, DA, or LOKE."""


class PartitionError(AetherError):
    """GPT parse failure, bad LUN, bad sector range."""


class AuthError(AetherError):
    """Firehose or Odin rejected our credentials."""


class DeviceGone(TransportError):
    """Device reset or unplugged mid-operation."""
