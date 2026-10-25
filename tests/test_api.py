"""HTTP API, using the offline Gemma replay and mock media. No network."""
from fastapi.testclient import TestClient

from storyteller.app import create_app
from storyteller.archive import ArchiveClient
from storyteller.skill import load_skill


def _client(settings):
    skill = load_skill(settings.skill_dir)
    archive = ArchiveClient(skill.verifier.FIELDS, settings.snapshot_path, settings.tap_url,
                            offline=True, cache_dir=settings.cache_dir)
    app = create_app(settings=settings, archive=archive, skill=skill)
    return TestClient(app)


def test_status_hides_secrets_and_reports_modes(settings):
    body = _client(settings).get("/api/status").json()
    assert body["llm"]["backend"] == "offline"
    assert body["media"]["mode"] == "mock"
    assert body["archive"]["catalog_rows"] > 6000
    assert body["skill"]["name"] == "exoplanet-story-grounding"
    blob = str(body).lower()
    assert "xai_api_key" not in blob
    assert "bearer" not in blob


def test_suggest_today_and_compare(settings):
    c = _client(settings)
    names = [r["pl_name"] for r in c.get("/api/suggest", params={"q": "trapp"}).json()["results"]]
    assert "TRAPPIST-1 e" in names
    today = c.get("/api/today").json()
    assert today["pl_name"]
    # Offline mode picks a planet we actually have a recording for.
    assert today["pl_name"] in {"55 Cnc e", "TRAPPIST-1 e", "Kepler-452 b", "HD 209458 b", "Proxima Cen b", "Kepler-785 b"}
    rows = c.get("/api/compare", params={"name": "TRAPPIST-1 e"}).json()["rows"]
    radius = next(r for r in rows if r["field"] == "pl_rade")
    assert radius["planet"] == 0.92 and radius["earth"] == 1.0
    hot = c.get("/api/compare", params={"name": "55 Cnc e"}).json()["rows"]
    insol = next(r for r in hot if r["field"] == "pl_insol")
    assert insol["summary"].startswith("2,658x")
    assert "e+" not in insol["summary"]


def test_search_is_safe_and_ordered(settings):
    c = _client(settings)
    found = c.post("/api/search", json={"query": "the hottest planet discovered in 2020"}).json()
    assert found["understood"] and found["parser"] == "rules"
    assert "disc_year = 2020" in found["adql"]
    assert "drop" not in found["adql"]
    assert found["results"][0]["pl_eqt"] >= found["results"][-1]["pl_eqt"]
    junk = c.post("/api/search", json={"query": "please just tell me a joke"}).json()
    assert junk["understood"] is False
    assert junk["adql"] is None
    assert junk["examples"]


def test_story_replay_retries_then_passes(settings):
    c = _client(settings)
    # Recorded gemma3:4b, strict prompt: seed 1 of 55 Cnc e invents "twenty-seven times"
    # and seed 2 is clean, so this exercises the real retry loop on real model output.
    body = c.post("/api/story", json={"name": "55 Cnc e", "session_id": "demo", "model": "gemma3:4b"}).json()
    assert body["origin"] == "model"
    assert body["verification"]["passed"]
    assert body["verification"]["score"] == 100
    assert body["attempts"][0]["passed"] is False
    assert body["attempts"][1]["passed"] is True
    assert body["llm"]["recorded"] is True
    assert "twenty-seven" not in body["story"].lower()
    assert "12.5855" in body["story"]
    told = c.get("/api/session", params={"session_id": "demo"}).json()["told"]
    assert told[0]["pl_name"] == "55 Cnc e"


def test_kid_mode_offline_uses_the_template(settings):
    c = _client(settings)
    body = c.post("/api/story", json={"name": "Kepler-785 b", "kid": True, "session_id": "kids"}).json()
    assert body["origin"] == "template"
    assert body["verification"]["passed"]
    assert body["planet"]["pl_bmasse"] is None
    assert "unknown" in body["story"].lower()


def test_image_and_narration_are_cached_files(settings):
    c = _client(settings)
    c.post("/api/story", json={"name": "HD 209458 b", "session_id": "media"})
    image = c.post("/api/image", json={"name": "HD 209458 b"}).json()
    assert image["mode"] == "mock"
    assert any(d["field"] == "st_teff" for d in image["derivation"])
    fetched = c.get(image["url"])
    assert fetched.status_code == 200
    assert "svg" in fetched.headers["content-type"]
    assert b"<svg" in fetched.content
    again = c.post("/api/image", json={"name": "HD 209458 b"}).json()
    assert again["cached"] is True and again["url"] == image["url"]
    audio = c.post("/api/narrate", json={"name": "HD 209458 b", "session_id": "media"}).json()
    wav = c.get(audio["url"])
    assert wav.status_code == 200
    assert wav.content[:4] == b"RIFF"
    assert "audio" in wav.headers["content-type"]


def test_errors_are_json(settings):
    c = _client(settings)
    missing = c.post("/api/story", json={"name": "Not A Planet"})
    assert missing.status_code == 404
    assert "error" in missing.json()
    bad_model = c.post("/api/story", json={"name": "TRAPPIST-1 e", "model": "claude"})
    assert bad_model.status_code == 400
    no_story = c.post("/api/narrate", json={"name": "TRAPPIST-1 e", "session_id": "nobody"})
    assert no_story.status_code == 400
    traversal = c.get("/media/..%2F.env")
    assert traversal.status_code == 404


def test_pages_load(settings):
    c = _client(settings)
    assert c.get("/").status_code == 200
    assert "every story is true" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/styles.css").status_code == 200
