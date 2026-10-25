"""The Agent Skill is loaded for real and is the prompt the harness sends."""
from pathlib import Path

import pytest

from storyteller.config import ROOT
from storyteller.skill import SkillError, load_skill, validate_frontmatter

SKILL = ROOT / "skills" / "exoplanet-story-grounding"


def test_frontmatter_matches_the_spec():
    skill = load_skill(SKILL)
    assert skill.name == "exoplanet-story-grounding"
    assert skill.name == SKILL.name
    assert 1 <= len(skill.meta["description"]) <= 1024
    assert skill.default_max_retries == 2
    for section in ("Role", "Story rules", "Kid-friendly mode", "Output format", "Retry feedback"):
        assert skill.sections[section].strip()
    assert (SKILL / "scripts" / "verifier.py").exists()
    assert (SKILL / "references" / "fields.md").exists()


def test_bad_frontmatter_is_rejected():
    assert validate_frontmatter({"name": "Wrong", "description": "x"}, "exoplanet-story-grounding")
    assert validate_frontmatter({"name": "ok-name", "description": ""}, "ok-name")
    assert validate_frontmatter({"name": "ok-name", "description": "x" * 1025}, "ok-name")
    assert not validate_frontmatter({"name": "ok-name", "description": "A skill."}, "ok-name")


def test_missing_file_raises():
    with pytest.raises(SkillError):
        load_skill(Path("/tmp/does-not-exist-skill"))


def test_system_prompt_is_the_skill_text(skill):
    prompt = skill.system_prompt(kid=False)
    assert skill.sections["Role"] in prompt
    assert skill.sections["Story rules"] in prompt
    assert "KID-FRIENDLY" not in prompt
    kid = skill.system_prompt(kid=True)
    assert skill.sections["Kid-friendly mode"] in kid
    user = skill.user_prompt({"pl_name": "X"}, feedback=["fix the year"])
    assert "FACTS:" in user and "fix the year" in user
    assert skill.sections["Retry feedback"] in user
