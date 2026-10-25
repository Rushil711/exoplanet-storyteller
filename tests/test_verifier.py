"""Verifier tests, including the team's original planted-error cases."""
import json
from pathlib import Path

import pytest

from storyteller.config import ROOT

GOOD = [
    "TRAPPIST-1 e, found in 2017, circles its M8.0 V star every 6.1 days at 0.02925 AU, 12.43 parsecs from Earth.",
    "It is 0.92 times Earth's radius with an equilibrium temperature of 249.7 K and 0.65 times Earth's insolation.",
]
BAD = [
    ("TRAPPIST-1 e is 12.43 light-years away.", "parsecs labelled as light-years"),
    ("It orbits every 7 days.", "wrong period"),
    ("Its star is a G2 star.", "wrong spectral class"),
    ("It has 3 moons.", "invented number"),
    ("Discovered in 2015.", "wrong year"),
    ("It orbits every six and a half days.", "wrong period in words"),
    ("The mass is 5 Earth masses.", "wrong mass"),
    ("The radius is 0.692 Earth radii.", "right number, wrong field"),
    ("The planet is 249.7 AU from its star.", "right number, wrong unit"),
]


def test_known_good_sentences_pass(skill, planets):
    t = planets["TRAPPIST-1 e"]
    for sentence in GOOD:
        verdict = skill.verify(sentence, t)
        assert verdict["n_unsupported"] == 0, sentence
        assert verdict["passed"], verdict["feedback"]


def test_planted_errors_are_caught(skill, planets):
    t = planets["TRAPPIST-1 e"]
    for sentence, why in BAD:
        verdict = skill.verify(sentence, t)
        assert verdict["n_unsupported"] > 0, why
        assert skill.verifier.feedback(verdict, t), why


def test_wrong_temperature_is_caught_even_with_a_bare_kelvin(skill, planets):
    # The team's first verifier missed this one: 5000 K is close to neither value
    # once the sentence is recognised as being about the planet, not the star.
    c = planets["55 Cnc e"]
    verdict = skill.verify("55 Cnc e is at 5000 K.", c)
    assert verdict["n_unsupported"] == 1
    assert any("1958" in line for line in verdict["feedback"])


def test_number_word_arithmetic_slip(skill, planets):
    k = planets["Kepler-785 b"]
    slipped = skill.verify("It receives sixteen times the starlight Earth receives.", k)
    assert slipped["n_unsupported"] == 1
    copied = skill.verify("It receives 163.862 times the starlight Earth receives.", k)
    assert copied["passed"]


def test_compound_number_words(skill, planets):
    k = planets["Kepler-785 b"]
    verdict = skill.verify("The starlight is one hundred sixty-three times the starlight Earth receives.", k)
    assert verdict["numbers"][0]["status"] in ("exact", "rounded")


def test_hedged_claim_passes_and_asserted_claim_does_not(skill, planets):
    t = planets["TRAPPIST-1 e"]
    hedged = skill.verify("Whether it has an atmosphere, no one knows.", t)
    assert hedged["passed"]
    asserted = skill.verify("It has a thick atmosphere of nitrogen.", t)
    assert "atmosphere" in asserted["qualitative_flags"]
    assert not asserted["passed"]


def test_wrong_discovery_method(skill, planets):
    t = planets["TRAPPIST-1 e"]
    verdict = skill.verify("Astronomers found it by watching the star wobble.", t)
    assert not verdict["passed"]
    assert verdict["method_flags"]


def test_feedback_names_the_archive_value(skill, planets):
    t = planets["TRAPPIST-1 e"]
    verdict = skill.verify("TRAPPIST-1 e is 12.43 light-years away.", t)
    text = " ".join(verdict["feedback"]).lower()
    assert "parsec" in text
    assert "do not convert" in text


def test_template_passes_for_the_whole_catalog(skill, planets):
    for rec in planets.values():
        for kid in (False, True):
            story = skill.template_story(rec, kid=kid)
            verdict = skill.verify(story, rec)
            assert verdict["passed"], (rec["pl_name"], kid, verdict["feedback"][:3])


def test_cli_exit_code(tmp_path, planets):
    rec = planets["TRAPPIST-1 e"]
    planet = tmp_path / "planet.json"
    story = tmp_path / "story.txt"
    planet.write_text(json.dumps(rec))
    story.write_text("It has 9 moons.")
    import subprocess
    import sys
    script = ROOT / "skills" / "exoplanet-story-grounding" / "scripts" / "verifier.py"
    proc = subprocess.run([sys.executable, str(script), str(planet), str(story)], capture_output=True, text=True)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["passed"] is False
    assert payload["feedback"]
