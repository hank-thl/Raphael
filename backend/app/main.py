from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.records import router as records_router
from app.core.config import get_settings
from app.db import DatabaseConnection


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = DatabaseConnection(get_settings())
    app.state.database_connection = connection
    try:
        connection.open()
        yield
    finally:
        connection.close()


app = FastAPI(title="Raphael API", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(records_router)


@app.middleware("http")
async def private_responses_no_store(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/admin/") or request.url.path.startswith("/auth/") or request.url.path == "/records" or request.url.path.startswith("/records/"):
        response.headers["Cache-Control"] = "no-store"
    return response



class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")
