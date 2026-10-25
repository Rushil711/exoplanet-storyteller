"""The write -> verify -> retry loop, with a fake model standing in for Gemma."""
from storyteller.archive import ArchiveClient
from storyteller.harness import Harness
from storyteller.llm import NoRecording
from storyteller.media import MediaService
from storyteller.memory import SessionMemory

from tests.conftest import FakeLLM, story_json

GOOD = ("TRAPPIST-1 e, found in 2017, circles its M8.0 V star every 6.1 days at 0.02925 AU, "
        "12.43 parsecs from Earth. It is 0.92 times Earth's radius with an equilibrium temperature of 249.7 K.")
BAD = "TRAPPIST-1 e has 3 moons, was discovered in 2015, and its star is a G2 star."


def _harness(settings, skill, replies, archive=None):
    if archive is None:
        archive = ArchiveClient(skill.verifier.FIELDS, settings.snapshot_path, settings.tap_url,
                                offline=True, cache_dir=settings.cache_dir)
    llm = FakeLLM(replies)
    media = MediaService(settings)
    memory = SessionMemory()
    return Harness(skill, archive, llm, media, memory, settings), llm


def test_retry_sends_verifier_feedback_then_accepts(settings, skill):
    harness, llm = _harness(settings, skill, [story_json(BAD), story_json(GOOD)])
    result = harness.tell("TRAPPIST-1 e", session_id="s", model="fake-model")
    assert result.origin == "model"
    assert result.verification["passed"]
    assert result.verification["score"] == 100
    assert [a.passed for a in result.attempts] == [False, True]
    retry = llm.calls[1]["prompt"]
    assert "RETRY FEEDBACK" in retry
    assert "2015" in retry and "G2" in retry
    assert "Never convert units" in llm.calls[0]["system"]
    # The system prompt is the skill file, not a copy that can drift.
    assert skill.sections["Story rules"].splitlines()[2] in llm.calls[0]["system"]


def test_exhausted_retries_fall_back_to_a_passing_template(settings, skill):
    harness, llm = _harness(settings, skill, [story_json(BAD)] * 5)
    result = harness.tell("TRAPPIST-1 e", model="fake-model")
    assert result.origin == "template"
    assert result.verification["passed"]
    assert result.fallback_reason
    assert len(result.attempts) == settings.max_retries + 1
    assert len(llm.calls) == settings.max_retries + 1
    assert "0.92" in result.story  # the real radius, from the record


def test_unreadable_reply_is_an_attempt_not_a_crash(settings, skill):
    harness, _llm = _harness(settings, skill, ["not json at all", story_json(GOOD)])
    result = harness.tell("TRAPPIST-1 e", model="fake-model")
    assert result.origin == "model"
    assert result.attempts[0].error
    assert "JSON" in result.attempts[0].feedback[0]


def test_no_recording_uses_the_template(settings, skill):
    harness, _llm = _harness(settings, skill, [NoRecording("no recording for this planet")])
    result = harness.tell("TRAPPIST-1 e", model="fake-model")
    assert result.origin == "template"
    assert result.verification["passed"]
    assert "no recording" in result.fallback_reason


def test_kid_mode_changes_the_system_prompt(settings, skill):
    harness, llm = _harness(settings, skill, [story_json(GOOD)])
    harness.tell("TRAPPIST-1 e", kid=True, model="fake-model")
    assert "KID-FRIENDLY MODE" in llm.calls[0]["system"]
    assert "aged 7 to 10" in llm.calls[0]["system"]


def test_session_memory_says_what_is_new(settings, skill):
    second = ("55 Cnc e was discovered in 2004 at McDonald Observatory. It orbits 55 Cnc, a G8 V star, "
              "every 0.74 days at 1958 K, 12.59 parsecs from Earth.")
    harness, _llm = _harness(settings, skill, [story_json(GOOD), story_json(second)])
    first = harness.tell("TRAPPIST-1 e", session_id="demo", model="fake-model")
    assert first.highlights == []
    second_result = harness.tell("55 Cnc e", session_id="demo", model="fake-model")
    kinds = {h["kind"] for h in second_result.highlights}
    assert "hottest" in kinds
    assert "shortest year" in kinds
    assert second_result.session_note
    assert "TRAPPIST-1 e" in second_result.session_note
    # The model is told the contrast in words, not given the other planet's numbers.
    assert "249.7" not in second_result.session_note


def test_search_builds_adql_and_runs_it_locally(settings, skill):
    harness, _llm = _harness(settings, skill, [])
    found = harness.search_planets("the hottest planet discovered in 2020; drop table pscomppars")
    assert found["understood"]
    assert found["parser"] == "rules"
    assert "drop" not in found["adql"].lower()
    assert found["source"] == "bundled snapshot"
    assert found["results"]
    assert all(r["disc_year"] == 2020 for r in found["results"])
    temps = [r["pl_eqt"] for r in found["results"]]
    assert temps == sorted(temps, reverse=True)


def test_unknown_planet_and_unknown_model(settings, skill):
    from storyteller.archive import ArchiveError
    harness, _llm = _harness(settings, skill, [])
    import pytest
    with pytest.raises(ArchiveError) as ei:
        harness.fetch_planet("Definitely Not Real 9")
    assert ei.value.status == 404
    with pytest.raises(ArchiveError):
        harness.tell("TRAPPIST-1 e", model="gpt-something")
