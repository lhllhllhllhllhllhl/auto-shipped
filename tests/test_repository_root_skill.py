from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_repository_root_is_a_skill_entrypoint() -> None:
    skill_md = PROJECT_ROOT / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "name: shangyu-order-converter" in text
    assert "description:" in text
    assert "skill/shangyu-order-converter/SKILL.md" in text
    assert (PROJECT_ROOT / "agents" / "openai.yaml").is_file()
    assert (PROJECT_ROOT / "skill" / "shangyu-order-converter" / "SKILL.md").is_file()
    assert (
        PROJECT_ROOT
        / "skill"
        / "shangyu-order-converter"
        / "scripts"
        / "doctor.py"
    ).is_file()
