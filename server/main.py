
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI

app = FastAPI(title="JARVIS AI Server", version="1.0.0")

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)
    web_search: bool = False
    language: Literal["tg", "ru", "en"] = "tg"


@app.get("/health")
def health():
    return {"status": "online", "service": "JARVIS"}


@app.post("/chat")
def chat(request: ChatRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="AI server is not configured"
        )

    language_names = {
        "tg": "Tajik",
        "ru": "Russian",
        "en": "English",
    }

    instructions = (
        "You are JARVIS, a helpful AI assistant. "
        "Answer clearly and accurately in "
        f"{language_names[request.language]}. "
        "If web search is enabled, use current web sources "
        "for recent news and provide source links when available. "
        "Never claim you searched the web if you did not."
    )

    conversation = [
        {"role": item.role, "content": item.content}
        for item in request.history
    ]
    conversation.append({
        "role": "user",
        "content": request.message
    })

    try:
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
            detail="The AI service could not complete the request"
        )
