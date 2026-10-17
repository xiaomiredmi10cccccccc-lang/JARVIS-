
import os
import hmac
from typing import Literal

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from openai import OpenAI

app = FastAPI(title="JARVIS AI Server", version="1.2.0")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=12,
    )
    web_search: bool = False
    language: Literal["tg", "ru", "en"] = "tg"


def verify_token(token: str | None) -> None:
    expected = os.environ.get("JARVIS_SERVER_TOKEN", "")

    if (
        not expected
        or not token
        or not hmac.compare_digest(token, expected)
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )


@app.get("/")
def root():
    return {
        "service": "JARVIS AI Server",
        "status": "online",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {
        "status": "online",
        "service": "JARVIS",
    }


@app.post("/chat")
def chat(
    request: ChatRequest,
    x_jarvis_token: str | None = Header(default=None),
):
    verify_token(x_jarvis_token)

    api_key = os.environ.get("OPENROUTER_API_KEY", "")

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
        f"Answer clearly and accurately in "
        f"{language_names[request.language]}. "
        "Do not claim to have searched the web "
        "unless you actually did."
    )

    messages = [
        {
            "role": "system",
            "content": instructions,
        }
    ]

    messages.extend(
        {
            "role": item.role,
            "content": item.content,
        }
        for item in request.history
    )

    messages.append(
        {
            "role": "user",
            "content": request.message,
        }
    )

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "X-Title": "JARVIS",
            },
        )

        response = client.chat.completions.create(
            model=os.environ.get(
                "OPENROUTER_MODEL",
                "openrouter/free",
            ),
            messages=messages,
        )

        answer = response.choices[0].message.content

        if not answer:
            raise HTTPException(
                status_code=502,
                detail="The AI returned an empty response",
            )

        return {
            "answer": answer,
            "web_search": False,
        }

    except HTTPException:
        raise

    except Exception as exc:
        print(
            "OpenRouter request failed: "
            f"{type(exc).__name__}"
        )

        raise HTTPException(
            status_code=502,
            detail="The AI service could not complete the request",
        )
