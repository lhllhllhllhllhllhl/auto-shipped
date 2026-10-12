from __future__ import annotations

import tempfile
import unittest

from auto_shipped.services.order_number_allocations import reserve_minute_sequence


class MinuteSequenceAllocationTests(unittest.TestCase):
    def test_purposes_are_isolated_and_same_source_rerun_is_reused(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
