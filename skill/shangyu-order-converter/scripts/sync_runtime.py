#!/usr/bin/env python3
"""Overlay a verified bundled runtime onto an existing configured state runtime."""

from __future__ import annotations

import json
import shutil

from runtime_common import (
    BUNDLED_PROJECT_ROOT,
    STATE_PROJECT_ROOT,
    verify_bundled_runtime,
)


def main() -> int:
    integrity = verify_bundled_runtime()
    if not integrity.get("ready"):
        print(
            json.dumps(
                {"status": "failed", "code": "BUNDLE_INTEGRITY_FAILED", "details": integrity},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    if not STATE_PROJECT_ROOT.is_dir():
        print(
            json.dumps(
                {
                    "status": "needs_setup",
                    "code": "STATE_RUNTIME_MISSING",
                    "next_action": "首次安装请运行bootstrap_runtime.py",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    shutil.copytree(BUNDLED_PROJECT_ROOT, STATE_PROJECT_ROOT, dirs_exist_ok=True)
    print(
        json.dumps(
            {
                "status": "synced",
                "source": str(BUNDLED_PROJECT_ROOT),
                "destination": str(STATE_PROJECT_ROOT),
                "preserved_dependencies": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
