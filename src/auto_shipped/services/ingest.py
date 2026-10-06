from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_shipped.catalog import ProductCatalog
from auto_shipped.domain import CanonicalOrder, Issue
from auto_shipped.source_adapters import TiantianWarehouseAdapter
from auto_shipped.validation import validate_order_batch


ADAPTERS = {
    TiantianWarehouseAdapter.adapter_id: TiantianWarehouseAdapter,
}


@dataclass(slots=True)
class IngestResult:
    profile_id: str
    orders: list[CanonicalOrder]
    batch_issues: list[Issue]

    def summary(self) -> dict[str, Any]:
        statuses = Counter(order.review_status for order in self.orders)
        issue_codes = Counter(
            issue.code
            for order in self.orders
            for issue in order.issues
        )
        issue_codes.update(issue.code for issue in self.batch_issues)
        return {
            "profile_id": self.profile_id,
            "order_count": len(self.orders),
            "status_counts": dict(sorted(statuses.items())),
            "issue_counts": dict(sorted(issue_codes.items())),
            "batch_issue_count": len(self.batch_issues),
        }


def load_profile(path: str | Path) -> dict[str, Any]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if profile.get("adapter") not in ADAPTERS:
        raise ValueError(f"未知来源适配器: {profile.get('adapter')}")
    return profile


def ingest_file(
    source_path: str | Path,
    catalog_csv: str | Path,
    profile_path: str | Path,
    mappings_path: str | Path,
) -> IngestResult:
    profile = load_profile(profile_path)
    catalog = ProductCatalog.from_files(catalog_csv, mappings_path)
    adapter = ADAPTERS[profile["adapter"]]()
    orders = adapter.ingest(source_path, profile, catalog)
    return IngestResult(
        profile_id=profile["profile_id"],
        orders=orders,
        batch_issues=validate_order_batch(orders),
    )

