from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, evals, invoices, master
from app.core.config import get_settings
from app.core.logging import configure_logging, log
from app.storage import get_storage


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    await get_storage().ensure_bucket()
    log.info("startup", env=settings.env)
    yield


app = FastAPI(title="FinOps Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(invoices.router, prefix="/api/v1")
app.include_router(master.router, prefix="/api/v1")
app.include_router(evals.router, prefix="/api/v1")
