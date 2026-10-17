import sys
import stat
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = PROJECT_ROOT / "skill" / "shangyu-order-converter" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from runtime_common import runtime_trust_status, state_root_status  # noqa: E402
from bootstrap_runtime import copy_runtime_tree, make_tree_owner_writable  # noqa: E402


def test_source_checkout_is_an_explicit_development_runtime() -> None:
    status = runtime_trust_status(PROJECT_ROOT)

    assert status["ready"] is True
    assert status["source"] == "external_development_checkout"
    assert isinstance(status["development_clean"], bool)


def test_unverified_runtime_copy_is_rejected(tmp_path: Path) -> None:
    copied_runtime = tmp_path / "auto-shipped"
    copied_runtime.mkdir()
    (copied_runtime / "pyproject.toml").write_text(
        "[project]\nname='auto-shipped'\n",
        encoding="utf-8",
    )
    (copied_runtime / "src" / "auto_shipped").mkdir(parents=True)
    (copied_runtime / "browser" / "guanyi").mkdir(parents=True)
    (copied_runtime / "browser" / "guanyi" / "geometry.mjs").write_text(
        "// locally patched runtime\n",
        encoding="utf-8",
    )

    status = runtime_trust_status(copied_runtime)

    assert status["ready"] is False
    assert status["code"] == "BUNDLE_INTEGRITY_FAILED"
    assert status["source"] == "unverified_runtime"


def test_writable_state_root_is_reported(tmp_path: Path) -> None:
    status = state_root_status(tmp_path / "state")

    assert status["ready"] is True
    assert status["code"] is None


def test_bootstrap_copy_makes_read_only_package_writable(tmp_path: Path) -> None:
    source = tmp_path / "readonly-source"
    nested = source / "browser" / "guanyi"
    nested.mkdir(parents=True)
    package_file = nested / "package.json"
    package_file.write_text('{"private":true}\n', encoding="utf-8")
    package_file.chmod(0o444)
    nested.chmod(0o555)
    (source / "browser").chmod(0o555)
    source.chmod(0o555)

    destination = tmp_path / "state-runtime"
    try:
        copy_runtime_tree(source, destination)

        assert stat.S_IMODE(destination.stat().st_mode) & stat.S_IWUSR
        assert stat.S_IMODE(
            (destination / "browser").stat().st_mode
        ) & stat.S_IWUSR
        assert stat.S_IMODE(
            (destination / "browser" / "guanyi").stat().st_mode
        ) & stat.S_IWUSR
        assert stat.S_IMODE(
            (
                destination
                / "browser"
                / "guanyi"
                / "package.json"
            ).stat().st_mode
        ) & stat.S_IWUSR
    finally:
        make_tree_owner_writable(source)
