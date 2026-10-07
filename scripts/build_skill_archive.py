#!/usr/bin/env python3
"""Build a directly uploadable Skill archive with SKILL.md at ZIP root."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import stat
import zipfile


REQUIRED_FRONTMATTER_KEYS = ("name", "description")
IGNORED_PARTS = {".DS_Store", "__pycache__", ".git"}


def _parse_frontmatter(skill_md: Path) -> dict[str, str]:
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")

    try:
        closing_index = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError("SKILL.md YAML frontmatter is not closed") from exc

    values: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")

    missing = [key for key in REQUIRED_FRONTMATTER_KEYS if not values.get(key)]
    if missing:
        raise ValueError(
            "SKILL.md frontmatter is missing required keys: " + ", ".join(missing)
        )
    return values


def _should_include(path: Path, skill_dir: Path) -> bool:
    relative = path.relative_to(skill_dir)
    if any(part in IGNORED_PARTS for part in relative.parts):
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return path.is_file()


def _archive_info(relative: Path, source: Path) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = source.stat().st_mode
    permissions = 0o755 if mode & stat.S_IXUSR else 0o644
    info.external_attr = (permissions & 0xFFFF) << 16
    info.create_system = 3
    return info


def build_archive(skill_dir: Path, output: Path) -> tuple[Path, str, int]:
    skill_dir = skill_dir.resolve()
    output = output.resolve()
    skill_md = skill_dir / "SKILL.md"

    if not skill_dir.is_dir():
        raise FileNotFoundError(f"Skill directory does not exist: {skill_dir}")
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md is not at Skill root: {skill_md}")
    _parse_frontmatter(skill_md)

    files = sorted(
        (path for path in skill_dir.rglob("*") if _should_include(path, skill_dir)),
        key=lambda path: path.relative_to(skill_dir).as_posix(),
    )
    if skill_md not in files:
        raise RuntimeError("SKILL.md was unexpectedly excluded from the archive")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.unlink(missing_ok=True)

    try:
        with zipfile.ZipFile(
            temporary_output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for source in files:
                relative = source.relative_to(skill_dir)
                archive.writestr(_archive_info(relative, source), source.read_bytes())

        with zipfile.ZipFile(temporary_output, "r") as archive:
            names = archive.namelist()
            if "SKILL.md" not in names:
                raise RuntimeError("Built archive does not contain SKILL.md at its root")
            if any(name.startswith(f"{skill_dir.name}/") for name in names):
                raise RuntimeError("Built archive contains an extra top-level Skill directory")
            if archive.testzip() is not None:
                raise RuntimeError("Built archive failed ZIP integrity verification")

        temporary_output.replace(output)
    finally:
        temporary_output.unlink(missing_ok=True)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return output, digest, len(files)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build a Skill ZIP whose archive root directly contains SKILL.md."
    )
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=project_root / "skill" / "shangyu-order-converter",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "dist" / "shangyu-order-converter.zip",
    )
    args = parser.parse_args()

    output, digest, file_count = build_archive(args.skill_dir, args.output)
    print(f"built={output}")
    print(f"files={file_count}")
    print(f"sha256={digest}")
    print("archive_root=SKILL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
