"""Transport boundary for remote Guanyi execution backends."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol


class RemoteTransport(Protocol):
    def invoke(
        self,
        action: str,
        operation_id: str,
        *,
        payload: bytes | None = None,
        stream: BinaryIO | None = None,
    ) -> dict: ...


def open_binary(path: Path) -> BinaryIO:
    return path.expanduser().resolve().open("rb")
