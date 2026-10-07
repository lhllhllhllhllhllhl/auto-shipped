from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_uploadable_skill_archive_has_skill_md_at_root(tmp_path: Path) -> None:
    output = tmp_path / "shangyu-order-converter.zip"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_skill_archive.py"),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "archive_root=SKILL.md" in completed.stdout
    assert output.is_file()
    assert output.with_suffix(".zip.sha256").is_file()

    with zipfile.ZipFile(output, "r") as archive:
        names = archive.namelist()
        assert "SKILL.md" in names
        assert "scripts/doctor.py" in names
        assert "runtime-manifest.json" in names
        assert not any(name.startswith("shangyu-order-converter/") for name in names)
        assert not any(name.startswith("auto-shipped/") for name in names)

        skill_md = archive.read("SKILL.md").decode("utf-8")
        assert skill_md.startswith("---\n")
        assert "name: shangyu-order-converter" in skill_md
        assert "description:" in skill_md
