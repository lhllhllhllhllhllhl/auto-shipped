"""Transport-only adapter for the single Guanyi browser executor."""

from .client import RemoteGuanyiClient
from .protocol import (
    REMOTE_STAGING_CONFIRMATION,
    ProtocolError,
    StageRequest,
)

__all__ = [
    "REMOTE_STAGING_CONFIRMATION",
    "ProtocolError",
    "RemoteGuanyiClient",
    "StageRequest",
]
