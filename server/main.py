
import os
import re
import hmac
import time
import threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
import psycopg
from psycopg.rows import dict_row
from pwdlib import PasswordHash

from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel, Field
from openai import OpenAI


RATE_LIMIT = 10
RATE_WINDOW = 60
AUTH_RATE_LIMIT = 5

request_times = defaultdict(deque)
auth_request_times = defaultdict(deque)
rate_lock = threading.Lock()

password_hash = PasswordHash.recommended()


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


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise HTTPException(
            status_code=503,
            detail="Database is not configured",
        )
    return url


def jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured",
        )
    return secret


def init_database() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not configured")
        return

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    email VARCHAR(254) NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        print("Database initialization successful")
    except Exception as exc:
        print("Database initialization failed:", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield


app = FastAPI(
    title="JARVIS AI Server",
    version="1.5.0",
    lifespan=lifespan,
)


def client_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


def apply_limit(
    store,
    ip: str,
    limit: int,
) -> None:
    now = time.monotonic()
    cutoff = now - RATE_WINDOW

    with rate_lock:
        timestamps = store[ip]

        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        if len(timestamps) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(RATE_WINDOW)},
            )

        timestamps.append(now)


def check_rate_limit(ip: str) -> None:
    apply_limit(request_times, ip, RATE_LIMIT)


def check_auth_rate_limit(ip: str) -> None:
    apply_limit(auth_request_times, ip, AUTH_RATE_LIMIT)


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


def normalize_email(email: str) -> str:
    email = email.strip().lower()

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(
            status_code=400,
            detail="Please provide a valid email address",
        )

    return email


def create_access_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=24),
    }

    return jwt.encode(
        payload,
        jwt_secret(),
        algorithm="HS256",
    )


def get_current_user(authorization: str | None):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
        )

    parts = authorization.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header",
        )

    try:
        payload = jwt.decode(
            parts[1],
            jwt_secret(),
            algorithms=["HS256"],
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    try:
        with psycopg.connect(
            database_url(),
            connect_timeout=10,
            row_factory=dict_row,
        ) as conn:
            user = conn.execute(
                "SELECT id, email, created_at FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        print("User lookup failed:", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Database is temporarily unavailable",
        )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    return user


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


@app.post("/auth/register", status_code=201)
def register(data: RegisterRequest, request: Request):
    check_auth_rate_limit(client_ip(request))
    email = normalize_email(data.email)

    try:
        hashed_password = password_hash.hash(data.password)

        with psycopg.connect(
            database_url(),
            connect_timeout=10,
            row_factory=dict_row,
        ) as conn:
            user = conn.execute(
                """
                INSERT INTO users (email, password_hash)
                VALUES (%s, %s)
                RETURNING id, email, created_at
                """,
                (email, hashed_password),
            ).fetchone()

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists",
        )
    except HTTPException:
        raise
    except Exception as exc:
        print("Registration failed:", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Registration service is temporarily unavailable",
        )

    token = create_access_token(user["id"], user["email"])

    return {
        "message": "Account created successfully",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "created_at": user["created_at"],
        },
    }


@app.post("/auth/login")
def login(data: LoginRequest, request: Request):
    check_auth_rate_limit(client_ip(request))
    email = normalize_email(data.email)

    try:
        with psycopg.connect(
            database_url(),
            connect_timeout=10,
            row_factory=dict_row,
        ) as conn:
            user = conn.execute(
                """
                SELECT id, email, password_hash, created_at
                FROM users
                WHERE email = %s
                """,
                (email,),
            ).fetchone()

    except HTTPException:
        raise
    except Exception as exc:
        print("Login database lookup failed:", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Login service is temporarily unavailable",
        )

    if not user or not password_hash.verify(
        data.password,
        user["password_hash"] if user else password_hash.hash(
            "invalid-password-placeholder"
        ),
    ):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
        )

    token = create_access_token(user["id"], user["email"])

    return {
        "message": "Login successful",
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "created_at": user["created_at"],
        },
    }


@app.get("/auth/me")
def me(authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)

    return {
        "id": user["id"],
        "email": user["email"],
        "created_at": user["created_at"],
    }


@app.post("/chat")
def chat(
    request: ChatRequest,
    http_request: Request,
    x_jarvis_token: str | None = Header(default=None),
):
    verify_token(x_jarvis_token)
    check_rate_limit(client_ip(http_request))

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
        {"role": item.role, "content": item.content}
        for item in request.history
    )

    messages.append(
        {"role": "user", "content": request.message}
    )

    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={"X-Title": "JARVIS"},
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
            "OpenRouter request failed:",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="The AI service could not complete the request",
        )
