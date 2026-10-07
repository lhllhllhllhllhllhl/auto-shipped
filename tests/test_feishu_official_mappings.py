from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path

from auto_shipped.catalog import ProductCatalog, ProductRecord
from auto_shipped.integrations.feishu import (
    OfficialMappingSyncError,
    build_mapping_key,
    build_official_mapping_snapshot,
    load_official_mapping_snapshot,
    validate_official_mapping_snapshot,
    write_official_mapping_snapshot,
)


def catalog() -> ProductCatalog:
    return ProductCatalog(
        [
            ProductRecord(
                product_code="JTW8E1",
                product_name="光明玉米礼盒",
                spec_code="6974768564811",
                spec_name="10根装",
                weight_grams=None,
                default_warehouse="上海尚舆商贸有限公司",
            )
        ]
    )


def record() -> dict[str, str]:
    values = {
        "mapping_id": "tiantian-product-code-2026dfyumi001",
        "company_id": "tiantian",
        "source_profile_id": "tiantian_warehouse_v2",
        "identifier_type": "product_code",
        "source_value": "2026DFYUMI001",
        "source_spec": "",
        "target_platform": "guanyi",
        "product_code": "JTW8E1",
        "spec_code": "6974768564811",
        "status": "active",
        "mapping_version": "1",
        "confirmed_by": "ou-test",
        "confirmed_at": "2026-09-30T00:00:00+08:00",
        "evidence": "用户确认",
    }
    values["mapping_key"] = build_mapping_key(
        company_id=values["company_id"],
        source_profile_id=values["source_profile_id"],
        identifier_type=values["identifier_type"],
        source_value=values["source_value"],
        source_spec=values["source_spec"],
        target_platform=values["target_platform"],
    )
    return values


class FeishuOfficialMappingTests(unittest.TestCase):
    def test_valid_rows_become_confirmed_product_mapping_snapshot(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
            synced_at="2026-10-01T00:00:00+00:00",
        )
        self.assertEqual(snapshot["mapping_revision"], "1")
        self.assertEqual(len(snapshot["mappings"]), 1)
        self.assertTrue(snapshot["mappings"][0]["confirmed"])
        self.assertEqual(snapshot["mappings"][0]["product_code"], "JTW8E1")
        self.assertEqual(len(snapshot["snapshot_sha256"]), 64)

    def test_changed_mapping_key_fails_closed(self) -> None:
        invalid = record()
        invalid["mapping_key"] = "mapping-tampered"
        with self.assertRaises(OfficialMappingSyncError):
            build_official_mapping_snapshot(
                [invalid],
                {"repository_status": "active", "mapping_revision": "1"},
                catalog(),
            )

    def test_unknown_catalog_pair_fails_closed(self) -> None:
        invalid = record()
        invalid["spec_code"] = "UNKNOWN"
        with self.assertRaises(OfficialMappingSyncError):
            build_official_mapping_snapshot(
                [invalid],
                {"repository_status": "active", "mapping_revision": "1"},
                catalog(),
            )

    def test_snapshot_round_trip_is_validated_before_use(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
            synced_at="2026-10-01T00:00:00+00:00",
        )
        self.assertEqual(
            validate_official_mapping_snapshot(snapshot, catalog())["mapping_revision"],
            "1",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official.json"
            write_official_mapping_snapshot(path, snapshot)
            loaded = load_official_mapping_snapshot(path, catalog())
            self.assertEqual(loaded["snapshot_sha256"], snapshot["snapshot_sha256"])

    def test_tampered_snapshot_fails_hash_validation(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
        )
        snapshot["mappings"][0]["spec_code"] = "TAMPERED"
        with self.assertRaises(OfficialMappingSyncError):
            validate_official_mapping_snapshot(snapshot, catalog())

    def test_native_payload_contract_builds_same_valid_snapshot(self) -> None:
        payload = {
            "metadata": {"repository_status": "active", "mapping_revision": "1"},
            "records": [record()],
        }
        restored = json.loads(json.dumps(payload, ensure_ascii=False))
        snapshot = build_official_mapping_snapshot(
            restored["records"],
            restored["metadata"],
            catalog(),
        )
        self.assertEqual(snapshot["mappings"][0]["source_value"], "2026DFYUMI001")


if __name__ == "__main__":
    unittest.main()
