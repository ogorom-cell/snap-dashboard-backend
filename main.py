from __future__ import annotations
"""
Snapchat Dashboard — FastAPI Backend
Run locally:  uvicorn main:app --reload
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import settings
from database import Base, engine
import scheduler as sched
from routers import auth, profiles, analytics, content


# Create all tables on startup (use Alembic for production migrations)
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched.start()
    yield
    sched.stop()


app = FastAPI(
    title="Snapchat Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,   # required so JWT cookie is sent cross-origin
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(auth.router,      prefix=API_PREFIX)
app.include_router(profiles.router,  prefix=API_PREFIX)
app.include_router(analytics.router, prefix=API_PREFIX)
app.include_router(content.router,   prefix=API_PREFIX)


@app.get("/")
def root():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}
