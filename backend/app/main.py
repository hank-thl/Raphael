from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Raphael API", version="0.1.0")


class HealthResponse(BaseModel):
    status: Literal["ok"]


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")
