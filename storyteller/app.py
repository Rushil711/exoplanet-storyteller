"""FastAPI app: one page, JSON endpoints, generated media.

Run:  uvicorn storyteller.app:app --host 0.0.0.0 --port 8741
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .archive import ArchiveClient, ArchiveError
from .config import Settings
from .harness import EXAMPLES, Harness
from .llm import LLMError, make_backend
from .logs import get_logger, register_secret, setup_logging
from .media import MediaError, MediaService, _ctype
from .memory import SessionMemory
from .skill import SkillError, load_skill

log = get_logger("app")
STATIC = Path(__file__).resolve().parent.parent / "static"
MEDIA_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*\.(svg|png|jpe?g|webp|mp3|wav)$")


class StoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    session_id: str | None = Field(default=None, max_length=80)
    kid: bool = False
    model: str | None = Field(default=None, max_length=64)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=400)


class ImageIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class NarrateIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    text: str | None = Field(default=None, max_length=5000)
    session_id: str | None = Field(default=None, max_length=80)
    voice: str | None = Field(default=None, max_length=64)


def create_app(settings: Settings | None = None, archive: ArchiveClient | None = None,
               llm=None, media: MediaService | None = None, skill=None,
               memory: SessionMemory | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    setup_logging(settings.log_file)
    register_secret(settings.xai_api_key)
    register_secret(settings.gemini_api_key)

    skill = skill or load_skill(settings.skill_dir)
    if archive is None:
        archive = ArchiveClient(
            fields=skill.verifier.FIELDS, snapshot_path=settings.snapshot_path, tap_url=settings.tap_url,
            timeout=settings.tap_timeout, offline=settings.archive_offline, cache_dir=settings.cache_dir)
        if not settings.archive_offline:
            threading.Thread(target=archive.refresh_catalog, name="catalog-refresh", daemon=True).start()
    llm = llm or make_backend(settings)
    media = media or MediaService(settings)
    memory = memory or SessionMemory()
    harness = Harness(skill, archive, llm, media, memory, settings)

    app = FastAPI(title="Exoplanet Storyteller", version="1.0.0")
    app.state.harness = harness
    app.state.settings = settings

    @app.exception_handler(ArchiveError)
    async def _archive(_req: Request, exc: ArchiveError):
        return JSONResponse({"error": exc.user_message}, status_code=exc.status)

    @app.exception_handler(MediaError)
    async def _media(_req: Request, exc: MediaError):
        return JSONResponse({"error": exc.user_message}, status_code=exc.status)

    @app.exception_handler(LLMError)
    async def _llm(_req: Request, exc: LLMError):
        return JSONResponse({"error": exc.user_message}, status_code=503)

    @app.exception_handler(SkillError)
    async def _skill(_req: Request, exc: SkillError):
        return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        health = llm.health()
        return {
            "app": "Exoplanet Storyteller",
            "tagline": "every story is true",
            "skill": {"name": skill.name, "version": skill.version, "description": skill.meta.get("description")},
            "llm": {"backend": llm.name, "model": llm.model, "label": getattr(llm, "label", llm.name),
                    "models": llm.models(), "ok": health.get("ok", False), "detail": health.get("detail", "")},
            "media": {"mode": settings.media_mode, "voice": settings.xai_voice_id},
            "archive": archive.status(),
            "max_retries": settings.max_retries,
            "examples": EXAMPLES,
        }

    @app.get("/api/suggest")
    def suggest(q: str = "", limit: int = 8):
        return {"results": archive.suggest(q, limit=max(1, min(limit, 20)))}

    @app.get("/api/today")
    def today():
        return harness.planet_of_the_day()

    @app.get("/api/compare")
    def compare(name: str):
        return harness.compare(name)

    @app.get("/api/voices")
    def voices():
        return {"voices": media.voices(), "mode": settings.media_mode}

    @app.get("/api/session")
    def session(session_id: str = ""):
        items = memory.told(session_id)
        return {"told": [{"pl_name": i["pl_name"], "score": i.get("score"), "origin": i.get("origin"),
                          "kid": i.get("kid")} for i in items]}

    @app.post("/api/search")
    def search(body: SearchIn):
        return harness.search_planets(body.query)

    @app.post("/api/story")
    def story(body: StoryIn):
        return harness.tell(body.name, session_id=body.session_id, kid=body.kid, model=body.model).to_dict()

    @app.post("/api/image")
    def image(body: ImageIn):
        return harness.generate_image(body.name)

    @app.post("/api/narrate")
    def narrate(body: NarrateIn):
        if body.text:
            return harness.narrate(body.text, voice=body.voice)
        if body.name:
            return harness.narrate_told(body.session_id, body.name, voice=body.voice)
        raise MediaError("Nothing to narrate.", 400)

    @app.get("/media/{name}")
    def media_file(name: str):
        if not MEDIA_NAME.match(name):
            return JSONResponse({"error": "Not found."}, status_code=404)
        path = Path(settings.cache_dir) / "media" / name
        if not path.is_file():
            return JSONResponse({"error": "Not found."}, status_code=404)
        return FileResponse(path, media_type=_ctype(path))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    log.info("app_ready", llm=llm.name, model=getattr(llm, "model", ""), media=settings.media_mode,
             planets=len(archive.catalog))
    return app


app = create_app()
