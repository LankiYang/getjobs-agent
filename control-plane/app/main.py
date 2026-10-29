"""getjobs control plane: auth + per-user isolated runners + channel proxy."""

import asyncio
import json
import queue
import threading
from pathlib import Path

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, resume, runners
from . import sites as site_service
from .channels import CHANNELS
from .config import ALLOWED_ORIGINS
from .db import get_conn, init_db
from .events import hub
from .security import (
    create_ticket,
    create_token,
    decode_ticket,
    decode_token,
    hash_password,
    verify_password,
)

app = FastAPI(title="getjobs control plane", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    init_db()
    hub.set_loop(asyncio.get_running_loop())


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
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def index():
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"}
    )


@app.post("/api/auth/register")
def register(body: Credentials) -> dict:
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "邮箱格式不正确")
    if len(body.password) < 8:
        raise HTTPException(400, "密码至少 8 位")
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


def _channel_state(data: dict | None) -> str:
    if not isinstance(data, dict):
        return "unknown"
    if data.get("isLoggedIn"):
        return "logged_in"
    login_state = data.get("loginState")
    if login_state == "qr_ready":
        return "pending_scan"
    if login_state == "qr_expired":
        return "qr_expired"
    if "browserReady" in data:
        return "not_logged_in" if data.get("browserReady") else "not_checked"
    return "not_logged_in"


@app.get("/api/channels/status")
def channels_status(user_id: int = Depends(current_user)) -> dict:
    runner = runners.status(user_id)
    running = bool(runner.get("running"))
    items = []
    for channel in CHANNELS.values():
        entry = {
            "key": channel.key,
            "label": channel.label,
            "maintained": channel.maintained,
            "loginQr": bool(channel.login_qr),
            "state": "runner_stopped" if not running else "unknown",
            "detail": None,
        }
        if running:
            try:
                response = runners.request(
                    user_id, "GET", f"{channel.prefix}/status", timeout=8
                )
                data = response.json() if response.status_code == 200 else None
                entry["state"] = _channel_state(data)
                entry["detail"] = data
            except (runners.RunnerError, httpx.HTTPError, ValueError):
                entry["state"] = "unknown"
        items.append(entry)
    return {"runnerRunning": running, "channels": items}


class SiteRequest(BaseModel):
    url: str
    name: str | None = None


@app.get("/api/sites")
def list_custom_sites(user_id: int = Depends(current_user)) -> dict:
    return {"sites": site_service.list_sites(user_id)}


@app.post("/api/sites")
def add_custom_site(body: SiteRequest, user_id: int = Depends(current_user)) -> dict:
    error = site_service.validate_url(body.url)
    if error:
        raise HTTPException(400, error)
    return site_service.upsert_site(user_id, body.name or body.url, body.url)


@app.delete("/api/sites/{site_id}")
def delete_custom_site(site_id: int, user_id: int = Depends(current_user)) -> dict:
    if not site_service.delete_site(user_id, site_id):
        raise HTTPException(404, "站点不存在")
    return {"ok": True}


@app.post("/api/sites/{site_id}/check")
def check_custom_site(site_id: int, user_id: int = Depends(current_user)) -> dict:
    site = site_service.get_site(user_id, site_id)
    if not site:
        raise HTTPException(404, "站点不存在")
    return site_service.check_site(user_id, site)


@app.post("/api/browser/takeover")
def browser_takeover(user_id: int = Depends(current_user)) -> dict:
    runners.ensure_vnc(user_id)
    ticket = create_ticket(user_id, "vnc", 120)
    return {
        "ticket": ticket,
        "viewPath": f"/assets/novnc/takeover.html?ticket={ticket}",
        "wsPath": f"/api/browser/takeover/ws?ticket={ticket}",
        "expiresIn": 120,
    }


@app.websocket("/api/browser/takeover/ws")
async def browser_takeover_ws(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket", "")
    user_id = decode_ticket(ticket, "vnc")
    if user_id is None:
        await websocket.close(code=4401)
        return
    port = runners.vnc_port_for_user(user_id)
    if port is None:
        await websocket.close(code=4404)
        return

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        await websocket.close(code=1011)
        return

    await websocket.accept(subprotocol="binary")

    async def ws_to_tcp():
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    tasks = [asyncio.create_task(ws_to_tcp()), asyncio.create_task(tcp_to_ws())]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()


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


@app.get("/api/ai/config")
def get_ai_config(user_id: int = Depends(current_user)):
    return _json(runners.request(user_id, "GET", "/api/ai/config"))


@app.post("/api/ai/config")
async def save_ai_config(request: Request, user_id: int = Depends(current_user)):
    payload = await request.json()
    return _json(runners.request(user_id, "POST", "/api/ai/config", json=payload))


@app.post("/api/channels/{channel}/resume-image")
async def upload_resume_image(
    channel: str,
    file: UploadFile = File(...),
    user_id: int = Depends(current_user),
):
    if channel != "boss":
        raise HTTPException(400, "当前仅 Boss 支持图片简历")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小不能超过 10MB")

    filename = file.filename or "resume"
    content_type = file.content_type or "application/octet-stream"
    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        try:
            content = resume.pdf_to_png(content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        filename = "resume.png"
        content_type = "image/png"
    elif not content_type.startswith("image/"):
        raise HTTPException(400, "请上传 PDF 或图片文件（png / jpg / gif）")

    response = runners.request(
        user_id,
        "POST",
        "/api/boss/config/resume-image",
        files={
            "file": (
                filename,
                content,
                content_type,
            )
        },
    )
    return _json(response)


@app.get("/api/channels/{channel}/resume-image/content")
def resume_image_content(channel: str, user_id: int = Depends(current_user)):
    if channel != "boss":
        raise HTTPException(400, "当前仅 Boss 支持图片简历")
    response = runners.request(
        user_id, "GET", "/api/boss/config/resume-image/content"
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "image/png"),
        headers={"Cache-Control": "no-store"},
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


@app.post("/api/agent/chat/stream")
async def agent_chat_stream(body: ChatRequest, user_id: int = Depends(current_user)):
    async def events():
        results: queue.Queue = queue.Queue()

        def worker():
            try:
                for event in agent.run_chat_stream(
                    user_id, body.session_id, body.message
                ):
                    results.put(("event", event))
            except agent.AgentNotConfigured as exc:
                results.put(("error", str(exc)))
            except httpx.HTTPError as exc:
                results.put(("error", f"模型调用失败: {exc}"))
            except Exception as exc:
                results.put(("error", str(exc)))
            finally:
                results.put(("end", None))

        threading.Thread(target=worker, daemon=True).start()

        while True:
            try:
                kind, payload = await asyncio.to_thread(results.get, True, 10)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if kind == "error":
                event = {"type": "error", "message": payload}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                break
            if kind == "end":
                break
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/agent/history")
def agent_history(session_id: str, user_id: int = Depends(current_user)) -> dict:
    return {"messages": agent.history(user_id, session_id)}


@app.get("/api/agent/events/stream")
async def agent_events(user_id: int = Depends(current_user)):
    queue = hub.subscribe(user_id)

    async def events():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            hub.unsubscribe(user_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/agent/health")
def agent_health(user_id: int = Depends(current_user)) -> dict:
    return {
        "configured": agent.configured(),
        "provider": agent.PROVIDER,
        "model": agent.MODEL if agent.configured() else None,
    }