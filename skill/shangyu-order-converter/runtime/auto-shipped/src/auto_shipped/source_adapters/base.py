from __future__ import annotations

from pathlib import Path
from typing import Protocol

from auto_shipped.catalog import ProductCatalog
from auto_shipped.domain import CanonicalOrder


class SourceAdapter(Protocol):
    def ingest(
        self,
        source_path: str | Path,
        profile: dict,
        catalog: ProductCatalog,
    ) -> list[CanonicalOrder]: ...

