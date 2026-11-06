from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.api.auth import router as auth_router

app = FastAPI(title="Raphael API", version="0.1.0")
app.include_router(auth_router)


@app.middleware("http")
async def auth_no_store(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response



class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")
