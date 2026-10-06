from __future__ import annotations

import unittest
from dataclasses import replace

from auto_shipped.integrations.feishu import (
    FeishuIdentity,
    ManagedPendingSheet,
    PendingMappingProposal,
    PendingSheetMetadata,
    UserSheetRoute,
    build_mapping_key,
    build_pending_mapping_proposal,
    resolve_or_provision_user_sheet,
    submit_pending_mapping_proposal,
)


NOW = "2026-10-01T08:00:00+00:00"


class FakeFeishuGateway:
    def __init__(self, identity: FeishuIdentity) -> None:
        self.identity = identity
        self.routes: list[UserSheetRoute] = []
        self.sheets: dict[str, ManagedPendingSheet] = {}
        self.proposals: dict[str, list[PendingMappingProposal]] = {}
        self.created_count = 0
        self.inject_duplicate_after_create = False
        self.drop_appended_proposals = False

    def get_current_identity(self) -> FeishuIdentity:
        return self.identity

    def list_user_routes(self) -> list[UserSheetRoute]:
        return list(self.routes)

    def upsert_user_route(self, route: UserSheetRoute) -> None:
        self.routes = [
            item
            for item in self.routes
            if item.feishu_user_id != route.feishu_user_id
        ]
        self.routes.append(route)

    def list_managed_pending_sheets(self) -> list[ManagedPendingSheet]:
        return list(self.sheets.values())

    def create_pending_sheet_from_template(
        self,
        *,
        title: str,
        template_sheet_id: str,
        provision_operation_id: str,
    ) -> ManagedPendingSheet:
        self.created_count += 1
        sheet_id = f"sheet-{self.created_count}"
        sheet = ManagedPendingSheet(sheet_id=sheet_id, title=title)
        self.sheets[sheet_id] = sheet
        self.proposals[sheet_id] = []
        return sheet

    def write_pending_sheet_metadata(
        self,
        sheet_id: str,
        metadata: PendingSheetMetadata,
    ) -> None:
        sheet = self.sheets[sheet_id]
        self.sheets[sheet_id] = replace(sheet, metadata=metadata)
        if self.inject_duplicate_after_create:
            duplicate_id = "sheet-concurrent"
            self.sheets[duplicate_id] = ManagedPendingSheet(
                sheet_id=duplicate_id,
                title="并发创建",
                metadata=metadata,
            )
            self.proposals[duplicate_id] = []

    def list_pending_proposals(self, sheet_id: str) -> list[PendingMappingProposal]:
        return list(self.proposals.get(sheet_id, []))

    def append_pending_proposal(
        self,
        sheet_id: str,
        proposal: PendingMappingProposal,
    ) -> None:
        if not self.drop_appended_proposals:
            self.proposals.setdefault(sheet_id, []).append(proposal)


def active_route(user_id: str, sheet_id: str) -> UserSheetRoute:
    return UserSheetRoute(
        feishu_user_id=user_id,
        sheet_id=sheet_id,
        sheet_title="待确认",
        status="active",
        schema_version="1.0",
        provision_key="feishu-user-test",
        provision_operation_id="provision-existing",
        created_at=NOW,
        last_used_at=NOW,
    )


def owned_sheet(user_id: str, sheet_id: str = "sheet-existing") -> ManagedPendingSheet:
    return ManagedPendingSheet(
        sheet_id=sheet_id,
        title="待确认_测试用户",
        metadata=PendingSheetMetadata(
            owner_user_id=user_id,
            schema_version="1.0",
            provision_key="feishu-user-test",
            created_by_operation="provision-existing",
        ),
    )


def proposal(user_id: str, *, product_code: str = "P1") -> PendingMappingProposal:
    return build_pending_mapping_proposal(
        company_id="tiantian",
        source_profile_id="tiantian_warehouse_v2",
        identifier_type="product_code",
        source_value="EXT-1",
        source_spec="",
        target_platform="guanyi",
        candidate_product_code=product_code,
        candidate_spec_code="S1",
        evidence="用户确认",
        base_official_revision="128",
        submitted_by=user_id,
        submission_operation_id=f"submit-{product_code}",
        submitted_at=NOW,
    )


class FeishuPendingMappingTests(unittest.TestCase):
    def test_mapping_key_is_normalized_and_deterministic(self) -> None:
        first = build_mapping_key(
            company_id="TIANTIAN",
            source_profile_id="tiantian_warehouse_v2",
            identifier_type="product_code",
            source_value=" EXT-1 ",
            source_spec="",
            target_platform="GUANYI",
        )
        second = build_mapping_key(
            company_id="tiantian",
            source_profile_id="tiantian_warehouse_v2",
            identifier_type="PRODUCT_CODE",
            source_value="ext-1",
            source_spec="",
            target_platform="guanyi",
        )
        self.assertEqual(first, second)

    def test_existing_route_and_owner_metadata_are_reused(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.routes.append(active_route("ou-user", "sheet-existing"))
        gateway.sheets["sheet-existing"] = owned_sheet("ou-user")

        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id="sheet-template",
            provision_operation_id="provision-1",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.sheet_id, "sheet-existing")
        self.assertFalse(result.created)

    def test_missing_route_is_repaired_from_sheet_owner_metadata(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.sheets["sheet-existing"] = owned_sheet("ou-user")

        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id="sheet-template",
            provision_operation_id="provision-2",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.repaired_route)
        self.assertEqual(gateway.routes[0].sheet_id, "sheet-existing")

    def test_unknown_user_is_auto_provisioned_and_read_back(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-new", display_name="新员工"))

        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id="sheet-template",
            provision_operation_id="provision-3",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "ready")
        self.assertTrue(result.created)
        self.assertEqual(gateway.created_count, 1)
        self.assertEqual(gateway.routes[0].status, "active")
        self.assertEqual(
            gateway.sheets[result.sheet_id].metadata.owner_user_id,
            "ou-new",
        )

    def test_shared_bot_identity_is_rejected(self) -> None:
        gateway = FakeFeishuGateway(
            FeishuIdentity("bot-app", identity_type="bot", display_name="机器人")
        )

        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id="sheet-template",
        )

        self.assertEqual(result.status, "needs_setup")
        self.assertEqual(result.code, "FEISHU_USER_IDENTITY_REQUIRED")
        self.assertEqual(gateway.created_count, 0)

    def test_duplicate_owned_sheets_fail_closed(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.sheets["sheet-a"] = owned_sheet("ou-user", "sheet-a")
        gateway.sheets["sheet-b"] = owned_sheet("ou-user", "sheet-b")

        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id="sheet-template",
            provision_operation_id="provision-4",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.code, "USER_PENDING_SHEET_CONFLICT")
        self.assertEqual(
            gateway.routes[0].conflict_sheet_ids,
            ("sheet-a", "sheet-b"),
        )

    def test_concurrent_creation_is_detected_after_create(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.inject_duplicate_after_create = True

        result = resolve_or_provision_user_sheet(
            gateway,
            template_sheet_id="sheet-template",
            provision_operation_id="provision-5",
            timestamp=NOW,
        )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.code, "USER_PENDING_SHEET_CONFLICT")

    def test_proposal_is_appended_and_exactly_read_back(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.routes.append(active_route("ou-user", "sheet-existing"))
        gateway.sheets["sheet-existing"] = owned_sheet("ou-user")
        gateway.proposals["sheet-existing"] = []
        item = proposal("ou-user")

        result = submit_pending_mapping_proposal(
            gateway,
            item,
            template_sheet_id="sheet-template",
        )

        self.assertEqual(result.status, "submitted")
        self.assertEqual(gateway.proposals["sheet-existing"], [item])

    def test_same_candidate_is_idempotent(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.routes.append(active_route("ou-user", "sheet-existing"))
        gateway.sheets["sheet-existing"] = owned_sheet("ou-user")
        item = proposal("ou-user")
        gateway.proposals["sheet-existing"] = [item]

        result = submit_pending_mapping_proposal(
            gateway,
            item,
            template_sheet_id="sheet-template",
        )

        self.assertEqual(result.status, "already_submitted")
        self.assertEqual(len(gateway.proposals["sheet-existing"]), 1)

    def test_different_target_for_same_mapping_key_is_recorded_as_conflict(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.routes.append(active_route("ou-user", "sheet-existing"))
        gateway.sheets["sheet-existing"] = owned_sheet("ou-user")
        gateway.proposals["sheet-existing"] = [proposal("ou-user", product_code="P1")]
        conflicting = proposal("ou-user", product_code="P2")

        result = submit_pending_mapping_proposal(
            gateway,
            conflicting,
            template_sheet_id="sheet-template",
        )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.code, "PENDING_MAPPING_CONFLICT")
        self.assertEqual(
            gateway.proposals["sheet-existing"][-1].proposal_status,
            "conflict",
        )

    def test_missing_write_readback_fails_closed(self) -> None:
        gateway = FakeFeishuGateway(FeishuIdentity("ou-user", display_name="员工"))
        gateway.routes.append(active_route("ou-user", "sheet-existing"))
        gateway.sheets["sheet-existing"] = owned_sheet("ou-user")
        gateway.proposals["sheet-existing"] = []
        gateway.drop_appended_proposals = True

        result = submit_pending_mapping_proposal(
            gateway,
            proposal("ou-user"),
            template_sheet_id="sheet-template",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "PROPOSAL_WRITE_NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
