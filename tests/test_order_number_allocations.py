from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace

from auto_shipped.integrations.feishu.daily_sequence_ledger import (
    DAILY_SEQUENCE_HEADERS,
    DAILY_SEQUENCE_SCHEMA_VERSION,
    DailySequenceLedgerConfig,
    DailySequenceLedgerError,
    DailySequenceLedgerRecord,
    LarkCliDailySequenceLedgerGateway,
)
from auto_shipped.services.order_number_allocations import (
    OrderNumberAllocationError,
    reserve_daily_sequence,
    reserve_minute_sequence,
)


class MemoryDailySequenceGateway:
    def __init__(self) -> None:
        self.records: list[DailySequenceLedgerRecord] = []
        self.fail_reads = False
        self.inject_overlap = False

    def current_user_id(self) -> str:
        return "ou_test_user"

    def list_records(self) -> list[DailySequenceLedgerRecord]:
        if self.fail_reads:
            raise DailySequenceLedgerError("offline")
        return list(self.records)

    def append_record(self, record: DailySequenceLedgerRecord) -> None:
        self.records.append(record)
        if self.inject_overlap:
            self.records.append(
                replace(
                    record,
                    allocation_id="c" * 20,
                    allocation_key="f" * 64,
                    operation_id="d" * 32,
                    source_sha256="f" * 64,
                )
            )


class CaptureLarkDailySequenceGateway(LarkCliDailySequenceLedgerGateway):
    def __init__(self) -> None:
        super().__init__(
            DailySequenceLedgerConfig(
                spreadsheet_token="sheet-token",
                annual_sheet_title_pattern="日序号_{year}",
                annual_sheet_row_count=10,
                annual_sheet_column_count=len(DAILY_SEQUENCE_HEADERS),
                auto_provision_annual_sheet=True,
            )
        )
        self.expected_record: DailySequenceLedgerRecord | None = None
        self.calls: list[tuple[str, ...]] = []

    def _ensure_annual_sheet(self, year: int) -> tuple[str, str]:
        return "sheet-id", f"日序号_{year}"

    def _read_rows(
        self,
        *,
        sheet_id: str,
        cell_range: str,
    ) -> list[tuple[int, list[str]]]:
        if cell_range == "A2:U10":
            return [(2, [""])]
        if cell_range == "A2:U2" and self.expected_record is not None:
            return [(2, [str(value) for value in self.expected_record.to_row()])]
        return []

    def _run(self, *args: str) -> dict[str, object]:
        self.calls.append(args)
        return {}


class MinuteSequenceAllocationTests(unittest.TestCase):
    def test_legacy_minute_purposes_are_isolated_and_rerun_is_reused(self) -> None:
        source_sha256 = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            platform = reserve_minute_sequence(
                purpose="platform_order_number",
                profile_id="sam_order_v1",
                prefix="SAM",
                batch_minute="2610121430",
                source_sha256=source_sha256,
                sequence_count=2,
                sequence_width=2,
                state_dir=directory,
            )
            member = reserve_minute_sequence(
                purpose="sam_buyer_member",
                profile_id="sam_order_v1",
                prefix="sam",
                batch_minute="2610121430",
                source_sha256=source_sha256,
                sequence_count=2,
                sequence_width=2,
                state_dir=directory,
            )
            member_rerun = reserve_minute_sequence(
                purpose="sam_buyer_member",
                profile_id="sam_order_v1",
                prefix="sam",
                batch_minute="2610121430",
                source_sha256=source_sha256,
                sequence_count=2,
                sequence_width=2,
                state_dir=directory,
            )

        self.assertEqual(platform.sequence_start, 1)
        self.assertEqual(member.sequence_start, 1)
        self.assertFalse(member.reused)
        self.assertTrue(member_rerun.reused)
        self.assertEqual(member.allocation_id, member_rerun.allocation_id)


class DailySequenceAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = MemoryDailySequenceGateway()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def reserve(
        self,
        *,
        purpose: str = "platform_order_number",
        profile_id: str = "profile_a",
        prefix: str = "RZD",
        business_date: str = "20261017",
        source_sha256: str = "a" * 64,
        sequence_count: int = 1,
        stamp_format: str = "%Y%m%d",
        sequence_separator: str = "",
        source_partition: str = "",
        gateway: MemoryDailySequenceGateway | None = None,
    ):
        return reserve_daily_sequence(
            purpose=purpose,
            profile_id=profile_id,
            company_id="company_a",
            prefix=prefix,
            business_date=business_date,
            source_sha256=source_sha256,
            sequence_count=sequence_count,
            sequence_width=3,
            stamp_format=stamp_format,
            sequence_separator=sequence_separator,
            source_partition=source_partition,
            state_dir=self.temporary.name,
            gateway=gateway or self.gateway,
        )

    def test_same_prefix_and_day_continue_across_source_profiles(self) -> None:
        first = self.reserve(sequence_count=2)
        second = self.reserve(
            profile_id="profile_b",
            source_sha256="b" * 64,
        )

        self.assertEqual(first.sequence_start, 1)
        self.assertEqual(first.sequence_end, 2)
        self.assertEqual(second.sequence_start, 3)

    def test_prefix_purpose_and_date_are_independent_namespaces(self) -> None:
        platform = self.reserve()
        other_prefix = self.reserve(prefix="TB", source_sha256="b" * 64)
        buyer_member = self.reserve(
            purpose="sam_buyer_member",
            prefix="RZD",
            source_sha256="c" * 64,
        )
        next_day = self.reserve(
            business_date="20261018",
            source_sha256="d" * 64,
        )

        self.assertEqual(platform.sequence_start, 1)
        self.assertEqual(other_prefix.sequence_start, 1)
        self.assertEqual(buyer_member.sequence_start, 1)
        self.assertEqual(next_day.sequence_start, 1)

    def test_same_source_rerun_next_day_reuses_original_date_and_range(self) -> None:
        first = self.reserve(sequence_count=2)
        repeated = self.reserve(business_date="20261018", sequence_count=2)

        self.assertEqual(repeated.allocation_id, first.allocation_id)
        self.assertEqual(repeated.business_date, "20261017")
        self.assertEqual(repeated.sequence_start, 1)
        self.assertTrue(repeated.reused)

    def test_same_source_rerun_keeps_original_rendering_after_rule_change(self) -> None:
        first = self.reserve(
            prefix="OLD",
            stamp_format="%y%m%d",
            sequence_separator="_",
        )
        repeated = self.reserve(
            prefix="NEW",
            business_date="20261018",
            stamp_format="%Y%m%d",
            sequence_separator="-",
        )

        self.assertEqual(repeated.allocation_id, first.allocation_id)
        self.assertEqual(repeated.prefix, "OLD")
        self.assertEqual(repeated.business_date, "20261017")
        self.assertEqual(repeated.stamp_format, "%y%m%d")
        self.assertEqual(repeated.sequence_separator, "_")

    def test_source_partition_separates_sam_product_types(self) -> None:
        corn = self.reserve(
            purpose="sam_buyer_member",
            prefix="samYM",
            source_partition="YM",
        )
        gloves = self.reserve(
            purpose="sam_buyer_member",
            prefix="samSH",
            source_partition="SH",
        )

        self.assertNotEqual(corn.allocation_id, gloves.allocation_id)
        self.assertEqual(corn.sequence_start, 1)
        self.assertEqual(gloves.sequence_start, 1)

    def test_same_source_changed_count_is_blocked(self) -> None:
        self.reserve(sequence_count=1)
        with self.assertRaisesRegex(OrderNumberAllocationError, "数量发生变化"):
            self.reserve(sequence_count=2)

    def test_same_source_conflicting_duplicate_is_blocked(self) -> None:
        self.reserve()
        self.gateway.records.append(
            replace(
                self.gateway.records[0],
                allocation_id="e" * 20,
                prefix="OTHER",
                namespace_key="platform_order_number|OTHER|20261017",
            )
        )
        with self.assertRaisesRegex(OrderNumberAllocationError, "不一致的重复占号"):
            self.reserve()

    def test_sequence_width_expands_after_999(self) -> None:
        first = self.reserve(sequence_count=999)
        second = self.reserve(source_sha256="b" * 64)

        self.assertEqual(first.sequence_end, 999)
        self.assertEqual(second.sequence_start, 1000)
        self.assertEqual(f"{second.sequence_start:03d}", "1000")

    def test_offline_only_reuses_confirmed_local_cache(self) -> None:
        first = self.reserve()
        self.gateway.fail_reads = True
        repeated = self.reserve()

        self.assertEqual(repeated.allocation_id, first.allocation_id)
        self.assertEqual(repeated.authority, "local_confirmed_cache")
        with self.assertRaisesRegex(OrderNumberAllocationError, "禁止从001离线起号"):
            self.reserve(source_sha256="b" * 64)

    def test_readback_overlap_blocks_result(self) -> None:
        self.gateway.inject_overlap = True
        with self.assertRaisesRegex(OrderNumberAllocationError, "重叠区间"):
            self.reserve()

    def test_ledger_schema_contains_no_recipient_pii_columns(self) -> None:
        self.assertFalse(
            {
                "recipient_name",
                "recipient_phone",
                "recipient_address",
                "source_file_name",
            }
            & set(DAILY_SEQUENCE_HEADERS)
        )

    def test_lark_append_is_non_overwriting_and_uses_full_schema(self) -> None:
        gateway = CaptureLarkDailySequenceGateway()
        record = DailySequenceLedgerRecord(
            allocation_id="c" * 20,
            allocation_key="a" * 64,
            business_date="20261017",
            purpose="platform_order_number",
            prefix="RZD",
            namespace_key="platform_order_number|RZD|20261017",
            company_id="rongzhida",
            source_profile_id="rongzhida_text_v1",
            source_sha256="b" * 64,
            sequence_start=1,
            sequence_end=1,
            sequence_count=1,
            sequence_width=3,
            operation_id="d" * 32,
            allocated_by="ou_test_user",
            allocated_at="2026-10-17T00:00:00+00:00",
            schema_version=DAILY_SEQUENCE_SCHEMA_VERSION,
            stamp_format="%Y%m%d",
        )
        gateway.expected_record = record

        gateway.append_record(record)

        self.assertEqual(len(record.to_row()), len(DAILY_SEQUENCE_HEADERS))
        self.assertEqual(len(gateway.calls), 1)
        self.assertIn("--allow-overwrite=false", gateway.calls[0])
        self.assertIn("A2:U2", gateway.calls[0])


if __name__ == "__main__":
    unittest.main()
