"""Thought → Speak — a personal AI communication coach (modular monolith).

Run:  uvicorn app:app --reload   then open http://127.0.0.1:8000   (run a single uvicorn process: the analysis
worker pool lives inside it)

This module is the composition root: it reads configuration, builds the domain objects, and mounts the routers.
  routes/auth.py        accounts            routes/practice.py   analysis jobs, audio, config
  routes/curriculum.py  lessons & programs  routes/progress.py   progress, history, comparisons
  coach/service.py      analysis orchestration   coach/jobs.py   jobs, quota, worker
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from coach.curriculum import Curriculum  # noqa: E402  (after load_dotenv)
from coach.jobs import JobRunner  # noqa: E402
from coach.service import AnalysisService  # noqa: E402
from coach.storage import Store  # noqa: E402
from routes import auth as auth_routes  # noqa: E402
from routes import curriculum as curriculum_routes  # noqa: E402
from routes import practice as practice_routes  # noqa: E402
from routes import progress as progress_routes  # noqa: E402
from routes.deps import Context  # noqa: E402

DB_PATH = Path(os.getenv("TTS_DB_PATH", BASE_DIR / "data" / "thought_to_speak.db"))
AUDIO_DIR = Path(os.getenv("TTS_AUDIO_DIR", DB_PATH.parent / "audio"))
TMP_DIR = Path(os.getenv("TTS_TMP_DIR", DB_PATH.parent / "tmp"))

store = Store(DB_PATH, legacy_json=BASE_DIR / "sessions.json")
curriculum = Curriculum(BASE_DIR / "content" / "journeys")
service = AnalysisService(curriculum, AUDIO_DIR, TMP_DIR)
runner = JobRunner(store, service)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runner.start()       # recover interrupted jobs and start the analysis workers (also started lazily)
    yield
    runner.stop()


# API docs (/docs, /redoc, /openapi.json) are on for local development; API_DOCS=0 hides them on a public server.
_docs = os.getenv("API_DOCS", "1").strip().lower() not in ("0", "false", "no", "off")
app = FastAPI(title="Thought → Speak", version="9.0-beta", lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url="/redoc" if _docs else None,
              openapi_url="/openapi.json" if _docs else None)
app.state.ctx = Context(store=store, curriculum=curriculum, service=service, runner=runner,
                        base_dir=BASE_DIR, audio_dir=AUDIO_DIR, tmp_dir=TMP_DIR)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
for module in (auth_routes, practice_routes, curriculum_routes, progress_routes):
    app.include_router(module.router)


@app.middleware("http")
async def revalidate_app_files(request: Request, call_next):
    """Make browsers re-check the page and its JS/CSS on every load (cheap: unchanged files return 304),
    so an update never runs with stale, cached scripts."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Security headers on every response. The CSP allows what the app actually uses: its own scripts plus the
# blob: audio worklet that captures the microphone, inline style attributes, and same-origin API/audio.
CSP = ("default-src 'self'; script-src 'self' blob:; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = os.getenv("SECURITY_HEADERS", "1").strip().lower() not in ("0", "false", "no", "off")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if SECURITY_HEADERS:
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        h.setdefault("X-Frame-Options", "DENY")
        if request.url.scheme == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/privacy")
def privacy():
    return FileResponse(BASE_DIR / "static" / "privacy.html")
