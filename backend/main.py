"""TarnishedBot API.

Run from the project root:
    uv run uvicorn backend.main:app --reload
"""

import logging
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.chat import DEFAULT_VOICE, ChatResult, HistoryMessage, Voice, run_chat
from backend.config import Settings, get_settings

log = logging.getLogger(__name__)

app = FastAPI(title="TarnishedBot API", version="0.2.0")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[HistoryMessage] = []
    voice: Voice = DEFAULT_VOICE


class ChatResponse(ChatResult):
    pass


def get_claude_client(settings: Annotated[Settings, Depends(get_settings)]) -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set. Add it to .env and restart the backend.",
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    client: Annotated[anthropic.Anthropic, Depends(get_claude_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatResponse:
    try:
        result = run_chat(
            client,
            settings.claude_model,
            request.message,
            request.history,
            database_url=settings.database_url,
            voice=request.voice,
        )
    except anthropic.AuthenticationError as exc:
        raise HTTPException(503, "The Anthropic API key was rejected. Check .env.") from exc
    except anthropic.RateLimitError as exc:
        raise HTTPException(429, "Rate limited by the Claude API. Try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error %s (request %s)", exc.status_code, exc.request_id)
        raise HTTPException(502, f"Claude API error ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(502, "Couldn't reach the Claude API.") from exc
    return ChatResponse(**result.model_dump())
