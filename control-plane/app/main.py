"""getjobs control plane: auth + per-user isolated runners + channel proxy."""

from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, runners
from .channels import CHANNELS
from .config import ALLOWED_ORIGINS
from .db import get_conn, init_db
from .security import create_token, decode_token, hash_password, verify_password

app = FastAPI(title="getjobs control plane", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.exception_handler(runners.RunnerError)
def _runner_error(_: Request, exc: runners.RunnerError) -> JSONResponse:
    return JSONResponse(
        {"message": exc.message, "detail": exc.detail},
        status_code=exc.status_code,
    )


class Credentials(BaseModel):
    email: str
    password: str


def current_user(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    user_id = decode_token(authorization.split(" ", 1)[1].strip())
    if user_id is None:
        raise HTTPException(401, "invalid token")
    return user_id


@app.get("/api/health")
def health() -> dict:
    return {"status": "UP", "service": "getjobs-control-plane"}


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/auth/register")
def register(body: Credentials) -> dict:
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 8:
        raise HTTPException(400, "邮箱格式不正确或密码少于 8 位")
    with get_conn() as conn:
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            raise HTTPException(409, "邮箱已注册")
        cursor = conn.execute(
            "INSERT INTO users(email, password_hash, created_at) VALUES(?, ?, datetime('now'))",
            (email, hash_password(body.password)),
        )
        user_id = cursor.lastrowid
    return {"token": create_token(user_id), "user": {"id": user_id, "email": email}}


@app.post("/api/auth/login")
def login(body: Credentials) -> dict:
    email = body.email.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "邮箱或密码错误")
    return {"token": create_token(row["id"]), "user": {"id": row["id"], "email": row["email"]}}


@app.get("/api/me")
def me(user_id: int = Depends(current_user)) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "user not found")
    return {"user": dict(row), "runner": runners.status(user_id)}


@app.post("/api/me/runner/start")
def start_runner(user_id: int = Depends(current_user)) -> dict:
    return runners.start(user_id)


@app.get("/api/me/runner")
def get_runner(user_id: int = Depends(current_user)) -> dict:
    return runners.status(user_id)


@app.post("/api/me/runner/stop")
def stop_runner(user_id: int = Depends(current_user)) -> dict:
    return runners.stop(user_id)


@app.get("/api/channels")
def list_channels(user_id: int = Depends(current_user)) -> dict:
    return {
        "channels": [
            {
                "key": channel.key,
                "label": channel.label,
                "loginQr": bool(channel.login_qr),
                "maintained": channel.maintained,
            }
            for channel in CHANNELS.values()
        ]
    }


def _channel(channel: str):
    entry = CHANNELS.get(channel)
    if not entry:
        raise HTTPException(404, f"unknown channel: {channel}")
    return entry


def _json(response):
    try:
        return JSONResponse(response.json(), status_code=response.status_code)
    except Exception:
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )


@app.get("/api/channels/{channel}/status")
def channel_status(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(runners.request(user_id, "GET", entry.endpoint("status")))


@app.get("/api/channels/{channel}/config")
def channel_config(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(runners.request(user_id, "GET", entry.endpoint("config")))


@app.put("/api/channels/{channel}/config")
async def update_channel_config(
    channel: str, request: Request, user_id: int = Depends(current_user)
):
    entry = _channel(channel)
    payload = await request.json()
    return _json(runners.request(user_id, "PUT", entry.endpoint("config"), json=payload))


@app.get("/api/channels/{channel}/config/options/{option_type}")
def channel_options(
    channel: str, option_type: str, user_id: int = Depends(current_user)
):
    entry = _channel(channel)
    return _json(
        runners.request(
            user_id, "GET", f"{entry.prefix}/config/options/{option_type}"
        )
    )


@app.get("/api/channels/{channel}/blacklist")
def channel_blacklist(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(
        runners.request(user_id, "GET", f"{entry.prefix}/config/blacklist")
    )


@app.post("/api/channels/{channel}/blacklist")
async def add_channel_blacklist(
    channel: str, request: Request, user_id: int = Depends(current_user)
):
    entry = _channel(channel)
    payload = await request.json()
    return _json(
        runners.request(
            user_id, "POST", f"{entry.prefix}/config/blacklist", json=payload
        )
    )


@app.delete("/api/channels/{channel}/blacklist/{item_id}")
def delete_channel_blacklist(
    channel: str, item_id: int, user_id: int = Depends(current_user)
):
    entry = _channel(channel)
    return _json(
        runners.request(
            user_id, "DELETE", f"{entry.prefix}/config/blacklist/{item_id}"
        )
    )


@app.post("/api/channels/{channel}/start")
def channel_start(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(runners.request(user_id, "POST", entry.endpoint("start")))


@app.post("/api/channels/{channel}/stop")
def channel_stop(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(runners.request(user_id, "POST", entry.endpoint("stop")))


@app.post("/api/channels/{channel}/logout")
def channel_logout(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(runners.request(user_id, "POST", entry.endpoint("logout")))


@app.get("/api/channels/{channel}/stats")
def channel_stats(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    return _json(runners.request(user_id, "GET", entry.endpoint("stats")))


@app.get("/api/channels/{channel}/list")
def channel_list(
    channel: str,
    page: int = 1,
    size: int = 20,
    statuses: str = "",
    keyword: str = "",
    user_id: int = Depends(current_user),
):
    entry = _channel(channel)
    params = {"page": page, "size": size}
    if statuses:
        params["statuses"] = statuses
    if keyword:
        params["keyword"] = keyword
    return _json(runners.request(user_id, "GET", entry.endpoint("list"), params=params))


@app.post("/api/channels/{channel}/login")
def channel_login(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    if not entry.login_open:
        raise HTTPException(400, "该渠道暂不支持接口打开登录页")
    return _json(runners.request(user_id, "POST", entry.login_open))


@app.get("/api/channels/{channel}/login/qr")
def channel_login_qr(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)
    if not entry.login_qr:
        raise HTTPException(400, "该渠道暂不支持二维码登录")
    response = runners.request(user_id, "GET", entry.login_qr)
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "image/png"),
    )


@app.get("/api/channels/{channel}/stream")
def channel_stream(channel: str, user_id: int = Depends(current_user)):
    entry = _channel(channel)

    def event_stream():
        with runners.request(
            user_id, "GET", f"{entry.prefix}/stream", stream=True
        ) as response:
            for chunk in response.iter_raw():
                if chunk:
                    yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.post("/api/agent/chat")
def agent_chat(body: ChatRequest, user_id: int = Depends(current_user)) -> dict:
    try:
        return agent.run_chat(user_id, body.session_id, body.message)
    except agent.AgentNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"模型调用失败: {exc}") from exc


@app.get("/api/agent/history")
def agent_history(session_id: str, user_id: int = Depends(current_user)) -> dict:
    return {"messages": agent.history(user_id, session_id)}


@app.get("/api/agent/health")
def agent_health(user_id: int = Depends(current_user)) -> dict:
    return {
        "configured": agent.configured(),
        "model": agent.MODEL if agent.configured() else None,
    }