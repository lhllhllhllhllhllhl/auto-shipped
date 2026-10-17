from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_shipped.catalog import ProductCatalog, ProductPhraseNormalizer, ProductRecord
from auto_shipped.integrations.feishu import (
    CANONICAL_MAPPING_MIGRATION_CONFIRMATION,
    FeishuIdentity,
    migrate_canonical_mapping_schema,
    plan_canonical_mapping_migration,
)
from auto_shipped.integrations.feishu.owner_publisher import MappingPublishLogRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def catalog() -> ProductCatalog:
    return ProductCatalog(
        [
            ProductRecord(
                "KKAW0290",
                "Yellow超软",
                "4548404200290",
                "超软",
                None,
                "测试仓",
            ),
            ProductRecord(
                "OTHER",
                "其他商品",
                "OTHER-SPEC",
                "其他规格",
                None,
                "测试仓",
            ),
        ],
        phrase_normalizer=ProductPhraseNormalizer.from_file(
            PROJECT_ROOT / "config/catalog/product_phrase_lexicon_v1.json"
        ),
    )


def mapping(*, mapping_id: str = "mapping-1", product_code: str = "KKAW0290", spec_code: str = "4548404200290") -> dict[str, str]:
    return {
        "mapping_id": mapping_id,
        "mapping_key": f"key-{mapping_id}",
        "company_id": "self_operated",
        "source_profile_id": "taojuzi_kqyd_text_v1",
        "identifier_type": "product_name",
        "source_value": "搓澡巾",
        "source_spec": "kikulon超软黄色",
        "target_platform": "guanyi",
        "product_code": product_code,
        "spec_code": spec_code,
        "status": "active",
        "mapping_version": "23",
        "confirmed_by": "owner",
        "confirmed_at": "2026-10-17T00:00:00+08:00",
        "evidence": "用户确认",
    }


class FakeMigrationGateway:
    def __init__(self) -> None:
        self.identity = FeishuIdentity("owner", "user", "Owner")
        self.metadata = {
            "repository_status": "active",
            "mapping_revision": "23",
        }
        self.records = [mapping()]
        self.schema_version = "1.0"
        self.migrated: list[dict[str, str]] = []
        self.logs: list[MappingPublishLogRecord] = []

    def get_current_identity(self) -> FeishuIdentity:
        return self.identity

    def get_official_repository_metadata(self) -> dict[str, str]:
        return dict(self.metadata)

    def update_official_repository_metadata(self, updates: Mapping[str, str]) -> None:
        self.metadata.update(updates)

    def list_official_mapping_records(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.records]

    def official_mapping_schema_version(self) -> str:
        return self.schema_version

    def migrate_official_mapping_canonical_fields(
        self, updates: Sequence[Mapping[str, str]]
    ) -> None:
        self.migrated = [dict(item) for item in updates]
        by_id = {item["mapping_id"]: item for item in self.records}
        for update in self.migrated:
            by_id[update["mapping_id"]].update(update)
        self.schema_version = "2.0"

    def upsert_mapping_publish_log(self, record: MappingPublishLogRecord) -> None:
        self.logs.append(record)


class CanonicalMappingMigrationTests(unittest.TestCase):
    def test_plan_detects_same_identity_pointing_to_different_targets(self) -> None:
        records = [
            mapping(),
            mapping(
                mapping_id="mapping-2",
                product_code="OTHER",
                spec_code="OTHER-SPEC",
            ),
        ]

        plan = plan_canonical_mapping_migration(
            records,
            catalog(),
            mapping_revision="23",
            sheet_schema_before="1.0",
        )

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0]["target_count"], 2)

    def test_missing_confirmation_does_not_write(self) -> None:
        gateway = FakeMigrationGateway()

        result = migrate_canonical_mapping_schema(
            gateway,
            catalog(),
            allowed_owner_user_ids=("owner",),
            confirmation_token="",
        )

        self.assertEqual(result["status"], "needs_confirmation")
        self.assertFalse(gateway.migrated)
        self.assertFalse(gateway.logs)

    def test_owner_confirmed_migration_writes_fields_and_increments_revision(self) -> None:
        gateway = FakeMigrationGateway()

        result = migrate_canonical_mapping_schema(
            gateway,
            catalog(),
            allowed_owner_user_ids=("owner",),
            confirmation_token=CANONICAL_MAPPING_MIGRATION_CONFIRMATION,
            operation_id="migration-test",
            timestamp="2026-10-17T00:00:00+00:00",
        )

        self.assertEqual(result["status"], "migrated")
        self.assertEqual(result["mapping_revision_after"], "24")
        self.assertEqual(gateway.migrated[0]["canonical_source_brand"], "kikulon")
        self.assertEqual(gateway.migrated[0]["canonical_source_product"], "搓澡巾")
        self.assertEqual(gateway.migrated[0]["canonical_source_spec"], "黄色|超软")
        self.assertEqual(gateway.metadata["repository_status"], "active")
        self.assertEqual(gateway.metadata["mapping_sheet_schema_version"], "2.0")
        self.assertEqual(gateway.logs[0].action, "MIGRATE_CANONICAL_SCHEMA")


if __name__ == "__main__":
    unittest.main()
