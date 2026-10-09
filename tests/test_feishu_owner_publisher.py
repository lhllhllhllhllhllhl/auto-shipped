from __future__ import annotations

import unittest
from dataclasses import replace

from auto_shipped.catalog import ProductCatalog, ProductRecord
from auto_shipped.integrations.feishu import (
    FeishuIdentity,
    ManagedPendingSheet,
    MappingPublishLogRecord,
    OfficialMappingRecord,
    PendingMappingProposal,
    PendingSheetMetadata,
    build_pending_mapping_proposal,
    plan_pending_mapping_publish,
    publish_pending_mappings,
)


OWNER = "ou-owner"
NOW = "2026-10-09T08:00:00+00:00"


class FakePublisherGateway:
    def __init__(self) -> None:
        self.identity = FeishuIdentity(OWNER, display_name="所有者")
        self.metadata = {
            "repository_status": "active",
            "mapping_revision": "2",
        }
        self.sheet = ManagedPendingSheet(
            sheet_id="pending-owner",
            title="待确认_所有者",
            metadata=PendingSheetMetadata(
                owner_user_id=OWNER,
                schema_version="1.0",
                provision_key="owner-key",
                created_by_operation="bootstrap",
            ),
        )
        self.proposals: list[PendingMappingProposal] = []
        self.official: list[dict[str, str]] = []
        self.logs: list[dict[str, str]] = []

    def get_current_identity(self) -> FeishuIdentity:
        return self.identity

    def list_managed_pending_sheets(self) -> list[ManagedPendingSheet]:
        return [self.sheet]

    def list_pending_proposals(self, sheet_id: str) -> list[PendingMappingProposal]:
        self.assert_sheet(sheet_id)
        return list(self.proposals)

    def update_pending_proposal_status(
        self, sheet_id: str, proposal_id: str, status: str
    ) -> None:
        self.assert_sheet(sheet_id)
        matches = [item for item in self.proposals if item.proposal_id == proposal_id]
        if len(matches) != 1:
            raise ValueError("proposal not unique")
        self.proposals = [
            replace(item, proposal_status=status)
            if item.proposal_id == proposal_id
            else item
            for item in self.proposals
        ]

    def assert_sheet(self, sheet_id: str) -> None:
        if sheet_id != self.sheet.sheet_id:
            raise ValueError("wrong sheet")

    def get_official_repository_metadata(self) -> dict[str, str]:
        return dict(self.metadata)

    def update_official_repository_metadata(self, updates: dict[str, str]) -> None:
        self.metadata.update(updates)

    def list_official_mapping_records(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.official]

    def upsert_official_mapping_record(self, record: OfficialMappingRecord) -> None:
        matches = [
            item for item in self.official if item["mapping_key"] == record.mapping_key
        ]
        if matches and matches != [record.to_dict()]:
            raise ValueError("conflicting official record")
        if not matches:
            self.official.append(record.to_dict())

    def update_official_mapping_status(self, mapping_key: str, status: str) -> None:
        matches = [item for item in self.official if item["mapping_key"] == mapping_key]
        if len(matches) != 1:
            raise ValueError("official record not unique")
        matches[0]["status"] = status

    def list_mapping_publish_logs(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.logs]

    def upsert_mapping_publish_log(self, record: MappingPublishLogRecord) -> None:
        matches = [item for item in self.logs if item["event_id"] == record.event_id]
        if matches and matches != [record.to_dict()]:
            raise ValueError("conflicting log")
        if not matches:
            self.logs.append(record.to_dict())

    def update_mapping_publish_log_status(self, event_id: str, status: str) -> None:
        matches = [item for item in self.logs if item["event_id"] == event_id]
        if len(matches) != 1:
            raise ValueError("log not unique")
        matches[0]["status"] = status


def catalog() -> ProductCatalog:
    return ProductCatalog(
        [
            ProductRecord(
                product_code="P1",
                product_name="商品一",
                spec_code="S1",
                spec_name="规格一",
                weight_grams=None,
                default_warehouse="",
            ),
            ProductRecord(
                product_code="P2",
                product_name="商品二",
                spec_code="S2",
                spec_name="规格二",
                weight_grams=None,
                default_warehouse="",
            ),
        ]
    )


def proposal(*, product_code: str = "P1", spec_code: str = "S1") -> PendingMappingProposal:
    return build_pending_mapping_proposal(
        company_id="company-a",
        source_profile_id="profile-a",
        identifier_type="product_name",
        source_value="来源商品",
        source_spec="蓝色",
        target_platform="guanyi",
        candidate_product_code=product_code,
        candidate_spec_code=spec_code,
        evidence="用户确认以后沿用",
        base_official_revision="2",
        submitted_by=OWNER,
        submission_operation_id=f"submit-{product_code}",
        submitted_at=NOW,
    )


def official_for(item: PendingMappingProposal, product_code: str, spec_code: str) -> dict[str, str]:
    return OfficialMappingRecord(
        mapping_id="existing",
        mapping_key=item.mapping_key,
        company_id=item.company_id,
        source_profile_id=item.source_profile_id,
        identifier_type=item.identifier_type,
        source_value=item.source_value,
        source_spec=item.source_spec,
        target_platform=item.target_platform,
        product_code=product_code,
        spec_code=spec_code,
        status="active",
        mapping_version="2",
        confirmed_by=OWNER,
        confirmed_at=NOW,
        evidence="existing",
    ).to_dict()


class FeishuOwnerPublisherTests(unittest.TestCase):
    def test_conflicting_official_target_blocks_entire_batch(self) -> None:
        gateway = FakePublisherGateway()
        item = proposal()
        gateway.proposals = [item]
        gateway.official = [official_for(item, "P2", "S2")]

        plan = plan_pending_mapping_publish(
            gateway,
            catalog(),
            allowed_owner_user_ids=[OWNER],
            allowed_company_ids=["company-a"],
            allowed_source_profile_ids=["profile-a"],
            timestamp=NOW,
        )

        self.assertEqual(plan.status, "blocked")
        self.assertEqual(plan.conflict_count, 1)
        self.assertEqual(gateway.metadata["mapping_revision"], "2")

    def test_exact_existing_mapping_is_skipped_without_revision_bump(self) -> None:
        gateway = FakePublisherGateway()
        item = proposal()
        gateway.proposals = [item]
        gateway.official = [official_for(item, "P1", "S1")]

        result = publish_pending_mappings(
            gateway,
            catalog(),
            allowed_owner_user_ids=[OWNER],
            allowed_company_ids=["company-a"],
            allowed_source_profile_ids=["profile-a"],
            confirmation_token="CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "published")
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.already_published_count, 1)
        self.assertEqual(gateway.metadata["mapping_revision"], "2")
        self.assertEqual(gateway.proposals[0].proposal_status, "published")

    def test_publish_is_versioned_logged_read_back_and_marks_proposal(self) -> None:
        gateway = FakePublisherGateway()
        gateway.proposals = [proposal()]

        result = publish_pending_mappings(
            gateway,
            catalog(),
            allowed_owner_user_ids=[OWNER],
            allowed_company_ids=["company-a"],
            allowed_source_profile_ids=["profile-a"],
            confirmation_token="CONFIRM_PUBLISH_ALL_PENDING_MAPPINGS",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "published")
        self.assertEqual(result.created_count, 1)
        self.assertEqual(gateway.metadata["mapping_revision"], "3")
        self.assertEqual(gateway.metadata["repository_status"], "active")
        self.assertEqual(gateway.metadata["publish_operation_id"], "")
        self.assertEqual(gateway.official[0]["status"], "active")
        self.assertEqual(gateway.official[0]["mapping_version"], "3")
        self.assertEqual(gateway.logs[0]["status"], "published")
        self.assertEqual(gateway.proposals[0].proposal_status, "published")

    def test_missing_confirmation_never_writes(self) -> None:
        gateway = FakePublisherGateway()
        gateway.proposals = [proposal()]

        result = publish_pending_mappings(
            gateway,
            catalog(),
            allowed_owner_user_ids=[OWNER],
            allowed_company_ids=["company-a"],
            allowed_source_profile_ids=["profile-a"],
            confirmation_token="",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "needs_confirmation")
        self.assertEqual(gateway.official, [])
        self.assertEqual(gateway.metadata["mapping_revision"], "2")


if __name__ == "__main__":
    unittest.main()
