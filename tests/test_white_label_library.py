from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from auto_shipped.catalog import (
    WhiteLabelLibraryError,
    load_white_label_library,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WhiteLabelLibraryTests(unittest.TestCase):
    def test_active_library_loads_confirmed_exact_skus(self) -> None:
        library = load_white_label_library(
            PROJECT_ROOT / "config/catalog/white_label_skus_v1.json"
        )
        self.assertEqual(library.library_id, "white_label_skus_v1")
        self.assertEqual(library.remark_token, "白标商品")
        self.assertEqual(
            library.sku_keys,
            frozenset(
                {
                    ("SWHL3627", "4901792023627"),
                    ("SWHL3610", "4901792023610"),
                    ("SWHL3603", "4901792023603"),
                }
            ),
        )

    def test_duplicate_exact_sku_fails_closed(self) -> None:
        payload = json.loads(
            (PROJECT_ROOT / "config/catalog/white_label_skus_v1.json").read_text(
                encoding="utf-8"
            )
        )
        payload["entries"].append(dict(payload["entries"][0]))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "white-label.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(WhiteLabelLibraryError):
                load_white_label_library(path)


if __name__ == "__main__":
    unittest.main()
