
import os
import hmac
import time
import threading
from collections import defaultdict, deque
from typing import Literal

from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel, Field
from openai import OpenAI

app = FastAPI(
    title="JARVIS AI Server",
    version="1.4.0",
)

# Temporary in-memory rate limiter:
# up to 10 requests per IP in 60 seconds.
RATE_LIMIT = 10
RATE_WINDOW = 60
request_times = defaultdict(deque)
rate_lock = threading.Lock()


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


def check_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    cutoff = now - RATE_WINDOW

    with rate_lock:
        timestamps = request_times[client_ip]

        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(RATE_WINDOW)},
            )

        timestamps.append(now)


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
    http_request: Request,
    x_jarvis_token: str | None = Header(default=None),
):
    verify_token(x_jarvis_token)

    # Use the direct client address. Do not trust forwarded
    # IP headers unless the trusted proxy is configured.
    client_ip = (
        http_request.client.host
        if http_request.client
        else "unknown"
    )
    check_rate_limit(client_ip)

    api_key = os.environ.get("OPENROUTER_API_KEY", "")

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI server is not configured",
        )

    model = os.environ.get(
        "OPENROUTER_MODEL",
        "openrouter/free",
    )

    instructions = {
        "tg": (
            "Ту JARVIS ҳастӣ — ёвари зеҳни сунъии муфид "
            "ва боодоб. Ба корбар бо забони тоҷикии равон "
            "ва табиӣ, бо алифбои кириллӣ ҷавоб деҳ. "
            "Имло ва сохти ҷумлаҳои тоҷикиро риоя кун. "
            "Ба савол мустақим ва фаҳмо ҷавоб деҳ. "
            "То вақте ки корбар нахоҳад, ҷавобро бо "
            "забонҳои русӣ ва англисӣ омехта накун. "
            "Агар ҷавобро надонӣ, ростқавлона бигӯ. "
            "Ҳеҷ гоҳ нагӯ, ки дар интернет ҷустуҷӯ кардӣ, "
            "агар воқеан ҷустуҷӯ накарда бошӣ."
        ),
        "ru": (
            "Ты JARVIS — полезный и вежливый ИИ-помощник. "
            "Отвечай ясно, естественно и по существу на русском. "
            "Если не знаешь ответа, честно скажи об этом. "
            "Не утверждай, что искал информацию в интернете, "
            "если поиск фактически не выполнялся."
        ),
        "en": (
            "You are JARVIS, a helpful and polite AI assistant. "
            "Answer clearly and accurately in English. "
            "If you do not know the answer, say so honestly. "
            "Do not claim to have searched the web unless "
            "a web search was actually performed."
        ),
    }

    messages = [
        {
            "role": "system",
            "content": instructions[request.language],
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
            model=model,
            messages=messages,
        )

        if not response.choices:
            raise HTTPException(
                status_code=502,
                detail="The AI returned no choices",
            )

        answer = response.choices[0].message.content

        if not answer or not answer.strip():
            raise HTTPException(
                status_code=502,
                detail="The AI returned an empty response",
            )

        return {
            "answer": answer.strip(),
            "web_search": False,
            "model": response.model or model,
        }

    except HTTPException:
        raise

    except Exception as exc:
        print(
            "OpenRouter request failed: "
            f"{type(exc).__name__}: {exc}"
        )

        raise HTTPException(
            status_code=502,
            detail="The AI service could not complete the request",
        )
