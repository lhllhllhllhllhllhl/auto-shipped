from __future__ import annotations

import hashlib
import unittest
import tempfile
import json
from pathlib import Path

from auto_shipped.catalog import ProductCatalog, ProductRecord
from auto_shipped.integrations.feishu import (
    OfficialMappingSyncError,
    build_item_expansion_rule_key,
    build_mapping_key,
    build_manual_store_route_key,
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
            ),
            ProductRecord(
                product_code="GMBN1",
                product_name="光明白糯玉米单根装",
                spec_code="6974768564804",
                spec_name="单根装",
                weight_grams=None,
                default_warehouse="上海尚舆商贸有限公司",
            ),
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


def store_route(*, store: str = "光明满元气") -> dict[str, str]:
    values = {
        "route_id": "route-jtw8e1",
        "scope_id": "manual_all",
        "product_code": "JTW8E1",
        "spec_code": "6974768564811",
        "store": store,
        "status": "active",
        "route_version": "1",
        "confirmed_by": "ou-test",
        "confirmed_at": "2026-10-15T00:00:00+08:00",
        "evidence": "用户确认",
    }
    values["route_key"] = build_manual_store_route_key(
        scope_id=values["scope_id"],
        product_code=values["product_code"],
        spec_code=values["spec_code"],
    )
    return values


def item_expansion_rows() -> list[dict[str, str]]:
    rule_key = build_item_expansion_rule_key(
        company_id="tiantian",
        source_profile_id="tiantian_warehouse_v2",
        identifier_type="product_code",
        source_value="BUNDLE-1",
        source_spec="",
        target_platform="guanyi",
    )
    common = {
        "rule_id": "tiantian-bundle-1",
        "rule_key": rule_key,
        "company_id": "tiantian",
        "source_profile_id": "tiantian_warehouse_v2",
        "identifier_type": "product_code",
        "source_value": "BUNDLE-1",
        "source_spec": "",
        "target_platform": "guanyi",
        "status": "active",
        "rule_version": "1",
        "confirmed_by": "ou-test",
        "confirmed_at": "2026-10-15T00:00:00+08:00",
        "evidence": "用户确认",
    }
    return [
        {
            **common,
            "component_id": "component-1",
            "component_index": "1",
            "product_code": "JTW8E1",
            "spec_code": "6974768564811",
            "quantity_multiplier": "1",
        },
        {
            **common,
            "component_id": "component-2",
            "component_index": "2",
            "product_code": "GMBN1",
            "spec_code": "6974768564804",
            "quantity_multiplier": "1",
        },
    ]


class FeishuOfficialMappingTests(unittest.TestCase):
    def test_item_expansions_share_the_official_versioned_snapshot(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
            item_expansion_records=item_expansion_rows(),
        )

        self.assertEqual(len(snapshot["item_expansions"]), 1)
        self.assertEqual(
            snapshot["item_expansions"][0]["components"][1]["product_code"],
            "GMBN1",
        )
        validated = validate_official_mapping_snapshot(snapshot, catalog())
        self.assertEqual(validated["item_expansions"], snapshot["item_expansions"])

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

    def test_manual_store_routes_share_the_official_versioned_snapshot(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
            manual_store_route_records=[store_route()],
        )

        self.assertEqual(len(snapshot["manual_store_routes"]), 1)
        self.assertEqual(snapshot["manual_store_routes"][0]["store"], "光明满元气")
        self.assertEqual(
            validate_official_mapping_snapshot(snapshot, catalog())[
                "manual_store_routes"
            ],
            snapshot["manual_store_routes"],
        )

    def test_duplicate_manual_store_route_key_fails_closed(self) -> None:
        with self.assertRaises(OfficialMappingSyncError):
            build_official_mapping_snapshot(
                [record()],
                {"repository_status": "active", "mapping_revision": "1"},
                catalog(),
                manual_store_route_records=[store_route(), store_route()],
            )

    def test_spec_only_row_is_supported_by_the_universal_schema(self) -> None:
        source = record()
        source["mapping_id"] = "sam-spec-only-swsh8096"
        source["company_id"] = "sam"
        source["source_profile_id"] = "sam_order_v1"
        source["identifier_type"] = "product_code"
        source["source_value"] = ""
        source["source_spec"] = "SWSH8096"
        source["mapping_key"] = build_mapping_key(
            company_id=source["company_id"],
            source_profile_id=source["source_profile_id"],
            identifier_type=source["identifier_type"],
            source_value=source["source_value"],
            source_spec=source["source_spec"],
            target_platform=source["target_platform"],
        )

        snapshot = build_official_mapping_snapshot(
            [source],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
        )

        self.assertEqual(snapshot["mappings"][0]["identifier_type"], "product_code")
        self.assertEqual(snapshot["mappings"][0]["source_value"], "")
        self.assertEqual(snapshot["mappings"][0]["source_spec"], "SWSH8096")

    def test_formal_snapshot_marks_mappings_as_future_reusable(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
        )

        self.assertEqual(snapshot["mappings"][0]["reuse_scope"], "future")
        self.assertEqual(
            snapshot["mappings"][0]["mapping_authority"],
            "feishu_official",
        )

    def test_blank_target_spec_is_allowed_only_for_unique_guanyi_product(self) -> None:
        source = record()
        source["product_code"] = "P-NO-SPEC"
        source["spec_code"] = ""
        product_catalog = ProductCatalog(
            [
                ProductRecord(
                    product_code="P-NO-SPEC",
                    product_name="无规格商品",
                    spec_code="",
                    spec_name="",
                    weight_grams=None,
                    default_warehouse="上海仓",
                )
            ]
        )

        snapshot = build_official_mapping_snapshot(
            [source],
            {"repository_status": "active", "mapping_revision": "1"},
            product_catalog,
        )

        self.assertEqual(snapshot["mappings"][0]["product_code"], "P-NO-SPEC")
        self.assertEqual(snapshot["mappings"][0]["spec_code"], "")

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

    def test_snapshot_without_future_reuse_authorization_is_rejected(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
        )
        snapshot["mappings"][0].pop("reuse_scope")
        unsigned = dict(snapshot)
        unsigned.pop("snapshot_sha256")
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()

        with self.assertRaises(OfficialMappingSyncError):
            validate_official_mapping_snapshot(snapshot, catalog())

    def test_snapshot_without_official_authority_is_rejected(self) -> None:
        snapshot = build_official_mapping_snapshot(
            [record()],
            {"repository_status": "active", "mapping_revision": "1"},
            catalog(),
        )
        snapshot["mappings"][0].pop("mapping_authority")
        unsigned = dict(snapshot)
        unsigned.pop("snapshot_sha256")
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()

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
