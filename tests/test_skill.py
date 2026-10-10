"""Tests validating Agent Skill compliance with the Agent Skill Open Standard."""

from pathlib import Path
import yaml


def test_agent_skill_file_exists():
    skill_path = Path(__file__).resolve().parent.parent / "skills" / "repo-repair" / "SKILL.md"
    assert skill_path.exists(), f"Skill file does not exist at {skill_path}"


def test_agent_skill_frontmatter_specification():
    skill_path = Path(__file__).resolve().parent.parent / "skills" / "repo-repair" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert content.startswith("---"), "SKILL.md must start with YAML frontmatter opening '---'"
    parts = content.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must contain opening and closing '---' frontmatter markers"

    frontmatter_raw = parts[1]
    meta = yaml.safe_load(frontmatter_raw)
    assert isinstance(meta, dict), "Frontmatter must parse into a YAML mapping dictionary"

    # Verify Agent Skill Open Standard mandatory fields
    assert meta.get("name") == "repo-repair", "Skill name must be 'repo-repair'"
    assert meta.get("description"), "Skill must provide a non-empty description"
    assert meta.get("version") == "1.0.0"
    assert meta.get("author") == "MDADILIFTEKHAR"
    assert meta.get("license") == "MIT"

    # Verify body instructions
    body = parts[2]
    assert "Untrusted Repository Contents" in body
    assert "Evidence-Gated Repair Workflow" in body
    assert "Deterministic Patch Gating" in body
    assert "Bounded 3-Attempt Loop" in body
