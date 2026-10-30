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


@app.middleware("http")
async def attach_llm_user_email(request, call_next):  # type: ignore[no-untyped-def]
    """Attribute spend and activity to the signed-in email.

    The Bearer-verified email wins; the client-sent X-User-Email is only a
    fallback for calls that carry no (valid) token.
    """
    from starlette.concurrency import run_in_threadpool

    from app.api import auth_guard
    from app.services.llm_call_context import reset_llm_user_email, set_llm_user_email

    auth = request.headers.get("authorization") or ""
    bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    # verified_email is cached per token but blocks on the first call, so keep it off the loop.
    verified = await run_in_threadpool(auth_guard.verified_email, bearer) if bearer else None
    email = verified or request.headers.get("x-user-email") or ""
    token = set_llm_user_email(email)
    try:
        return await call_next(request)
    finally:
        reset_llm_user_email(token)


@app.middleware("http")
async def attach_tweak_source(request, call_next):  # type: ignore[no-untyped-def]
    """Mark manuscript-changing requests so a save can tell a person from a pipeline."""
    from app.services.proposal_tweaks import classify_proposal_request, tweak_source_scope

    source = classify_proposal_request(request.method, request.url.path)
    if not source:
        return await call_next(request)
    with tweak_source_scope(source):
        return await call_next(request)


app.include_router(api_router)
app.include_router(auth_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/api/v1/health",
    }
