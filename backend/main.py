"""TarnishedBot API.

Run from the project root:
    uv run uvicorn backend.main:app --reload
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="TarnishedBot API", version="0.1.0")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    # Stub: echoes the message back. Will call Claude (with tools) later.
    return ChatResponse(reply=request.message)
