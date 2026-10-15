
import os
import secrets
import hmac
from typing import Literal

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from openai import OpenAI

app = FastAPI(title="JARVIS AI Server", version="1.1.0")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    web_search: bool = False
    language: Literal["tg", "ru", "en"] = "tg"


def verify_token(token: str | None) -> None:
    expected = os.environ.get("JARVIS_SERVER_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"status": "online", "service": "JARVIS"}


@app.post("/chat")
def chat(
    request: ChatRequest,
    x_jarvis_token: str | None = Header(default=None),
):
    verify_token(x_jarvis_token)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI server is not configured",
        )

    language_names = {
        "tg": "Tajik",
        "ru": "Russian",
        "en": "English",
    }

    instructions = (
        "You are JARVIS, a helpful AI assistant. "
        f"Answer clearly and accurately in {language_names[request.language]}. "
        "Use web search for current news only when it is enabled. "
        "Never claim to have searched the web if you did not."
    )

    conversation = [
        {"role": item.role, "content": item.content}
        for item in request.history
    ]
    conversation.append({"role": "user", "content": request.message})

    try:
        client = OpenAI(api_key=api_key)
        options = {
            "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            "instructions": instructions,
            "input": conversation,
        }

        if request.web_search:
            options["tools"] = [{"type": "web_search"}]

        response = client.responses.create(**options)

        return {
            "answer": response.output_text,
            "web_search": request.web_search,
        }
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="The AI service could not complete the request",
        )
