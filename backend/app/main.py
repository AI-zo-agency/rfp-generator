from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.auth import router as auth_router
from app.core.config import settings
from app.core.langsmith_tracing import configure_langsmith_tracing
from app.services.proposal_repository import init_proposal_db

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

# Sync LANGSMITH_* into os.environ before any LangChain / LangGraph import work.
configure_langsmith_tracing()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_langsmith_tracing()
    init_proposal_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

try:
    from langsmith.middleware import TracingMiddleware

    app.add_middleware(TracingMiddleware)
except Exception:  # noqa: BLE001 — optional if older langsmith
    logging.getLogger(__name__).debug(
        "LangSmith TracingMiddleware unavailable", exc_info=True
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list + [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(auth_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/api/v1/health",
    }
