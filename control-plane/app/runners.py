"""Per-user runner lifecycle: one isolated get_jobs process per account."""

import contextlib
import datetime as dt
import errno
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx

from . import activity
from . import config
from .channels import CHANNELS
from .db import get_conn

WINDOWS = os.name == "nt"
VNC_PORT_START = int(os.environ.get("RUNNER_VNC_PORT_START", "5900"))
MAX_RUNNERS = int(os.environ.get("RUNNER_MAX_CONCURRENT", "1"))
MIN_AVAILABLE_MIB = int(os.environ.get("RUNNER_MIN_AVAILABLE_MIB", "1024"))
if MAX_RUNNERS < 1 or MIN_AVAILABLE_MIB < 0:
    raise ValueError("RUNNER_MAX_CONCURRENT must be positive and RUNNER_MIN_AVAILABLE_MIB nonnegative")


class RunnerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 409, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _row(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM runners WHERE user_id = ?", (user_id,)).fetchone()


def _update(user_id: int, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE runners SET {assignments} WHERE user_id = ?", values)


def _log_tail(path: Path, lines: int = 40) -> str:
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 128 * 1024))
            content = stream.read().decode('utf-8', errors='replace').splitlines()
        return "\n".join(content[-lines:])
    except Exception:
        return ""


def _spawn(command: list[str], cwd: str, env: dict, log_file) -> subprocess.Popen:
    kwargs = {
        "cwd": cwd,
        "env": env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }
    if not WINDOWS:
        return subprocess.Popen(command, **kwargs)
    group = subprocess.CREATE_NEW_PROCESS_GROUP
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        return subprocess.Popen(command, creationflags=group | breakaway, **kwargs)
    except OSError:
        return subprocess.Popen(command, creationflags=group, **kwargs)


def _headers(row) -> dict:
    return {"X-Offerbuddy-Worker-Secret": row["secret"]}


def healthy(row) -> bool:
    if not row:
        return False
    try:
        response = httpx.get(
            f"http://127.0.0.1:{row['port']}/api/health",
            headers=_headers(row),
            timeout=3,
        )
        return response.status_code == 200
    except Exception:
        return False


def _allocate_port() -> int:
    with get_conn() as conn:
        used = {row["port"] for row in conn.execute("SELECT port FROM runners")}
    for port in range(config.PORT_START, config.PORT_END + 1):
        if port in used:
            continue
        if _port_free(port):
            return port
    raise RunnerError("没有可用的 runner 端口", status_code=503)


@contextlib.contextmanager
def _admission_lock():
    """Serialize admission across control-plane processes sharing this database."""
    lock_path = config.CONTROL_DB.with_name(config.CONTROL_DB.name + ".runner-admission")
    try:
        conn = sqlite3.connect(lock_path, timeout=max(10, config.START_TIMEOUT + 10))
    except sqlite3.Error as exc:
        raise RunnerError("无法进行 runner 容量检查", status_code=503) from exc
    try:
        try:
            conn.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise RunnerError("runner 容量检查繁忙，请稍后重试", status_code=503) from exc
        yield
    finally:
        conn.close()


def _available_memory_mib() -> int | None:
    if WINDOWS:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total_phys", "avail_phys", "total_page", "avail_page",
                    "total_virtual", "avail_virtual", "avail_extended_virtual",
                )
            ]

        state = MemoryStatus()
        state.length = ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return state.avail_phys // (1024 * 1024)
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _check_capacity() -> None:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM runners").fetchall()
    # A stopped row without a PID is normally historical. Probe its port before
    # making an HTTP request, since older shutdowns could have lost a live PID.
    live = 0
    for row in rows:
        if row["pid"] and _alive(row["pid"]):
            live += 1
        elif (row["status"] != "stopped" or row["pid"] or not _port_free(row["port"])) and healthy(row):
            live += 1
        if live >= MAX_RUNNERS:
            break
    if live >= MAX_RUNNERS:
        raise RunnerError("runner 数量已达到容量上限，请稍后重试", status_code=503)
    if MIN_AVAILABLE_MIB:
        available = _available_memory_mib()
        if available is None:
            raise RunnerError("无法检查可用内存，暂时不能启动 runner", status_code=503)
        if available < MIN_AVAILABLE_MIB:
            raise RunnerError("可用内存不足，暂时不能启动 runner", status_code=503)


def _clear_template_bio(database: Path) -> None:
    """Drop the sample bio shipped in the packaged DB template.

    The upstream template fills ai.introduce with its author's own tech stack.
    A new account owns none of that text, and an AI greeting built from it would
    describe someone else. Start empty; user data is never touched because this
    only runs on the freshly copied template.
    """
    try:
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE ai SET introduce = ''")
    except sqlite3.Error:
        # A template without an ai table is not a reason to block startup.
        pass


def _ensure_layout(data_dir: Path) -> None:
    (data_dir / "db").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    database = data_dir / "db" / "getjobs.db"
    if not database.exists():
        if not config.DB_TEMPLATE.is_file():
            raise RunnerError(
                "缺少数据库模板，请从 get_jobs release 下载 getjobs.db.template 并设置 GETJOBS_DB_TEMPLATE",
                status_code=503,
            )
        shutil.copy2(config.DB_TEMPLATE, database)
        _clear_template_bio(database)


def start(user_id: int) -> dict:
    activity.begin_busy(user_id)
    try:
        with activity.guard(user_id):
            row = _row(user_id)
            if row and healthy(row):
                return status(user_id)
            with _admission_lock():
                return _start(user_id)
    finally:
        activity.end_busy(user_id)


def _start(user_id: int) -> dict:
    row = _row(user_id)
    if row and healthy(row):
        _update(user_id, status="running")
        return status(user_id)

    if row and row["pid"] and _alive(row["pid"]):
        # Never hold the global admission lock while waiting for an uncertain
        # existing process; it may be unhealthy or the PID may have been reused.
        raise RunnerError("runner 进程仍在运行但尚未就绪，请稍后重试", status_code=409)

    if not config.GETJOBS_JAR.is_file():
        raise RunnerError(f"未找到 runner jar: {config.GETJOBS_JAR}", status_code=503)
    if not config.PATCHRIGHT_DIR.is_dir():
        raise RunnerError(f"未找到 patchright driver: {config.PATCHRIGHT_DIR}", status_code=503)

    _check_capacity()

    data_dir = Path(row["data_dir"]) if row else config.RUNNERS_ROOT / f"u{user_id}"
    _ensure_layout(data_dir)

    port = row["port"] if row else _allocate_port()
    secret = row["secret"] if row else secrets.token_urlsafe(48)

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO runners(user_id, port, secret, status, data_dir, created_at)
               VALUES(?, ?, ?, 'starting', ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 port = excluded.port,
                 secret = excluded.secret,
                 status = 'starting',
                 pid = NULL,
                 data_dir = excluded.data_dir""",
            (user_id, port, secret, str(data_dir), _now()),
        )

    env = os.environ.copy()
    env["OB_WORKER_SECRET"] = secret
    node = config.node_bin()
    if node:
        env["PLAYWRIGHT_NODEJS_PATH"] = node
    if os.environ.get("RUNNER_HEADLESS", "").strip().lower() in ("1", "true", "yes"):
        env["OB_BROWSER_HEADLESS"] = "1"

    wrapper = os.environ.get("RUNNER_WRAPPER", "").strip()
    if wrapper:
        command = [wrapper, str(port), str(data_dir), str(config.GETJOBS_JAR)]
    else:
        command = [
            config.java_bin(),
            f"-Dplaywright.cli.dir={config.PATCHRIGHT_DIR}",
            "-Dfile.encoding=UTF-8",
            "-Dsun.stdout.encoding=UTF-8",
            "-Dsun.stderr.encoding=UTF-8",
            "-jar",
            str(config.GETJOBS_JAR),
            f"--server.port={port}",
            "--server.address=127.0.0.1",
        ]

    log_path = data_dir / "logs" / "runner.log"
    _kill_vnc_for_port(port)
    with open(log_path, "ab") as log_file:
        process = _spawn(command, str(data_dir), env, log_file)
    _update(user_id, pid=process.pid, started_at=_now())

    deadline = time.time() + config.START_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            break
        if healthy(_row(user_id)):
            _update(user_id, status="running")
            return status(user_id)
        time.sleep(2)

    _update(user_id, status="error")
    raise RunnerError("runner 启动失败", status_code=502, detail=_log_tail(log_path))


def _alive(pid: int) -> bool:
    if WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        try:
            # kill(pid, 0) also succeeds for an unreaped Linux zombie. Parse
            # after the last ')' because process names can contain spaces or ')'.
            stat = Path(f"/proc/{pid}/stat").read_text()
            if stat.rsplit(")", 1)[1].split()[0] in ("Z", "X"):
                return False
        except (OSError, IndexError):
            pass  # Missing/inaccessible procfs is not evidence of death.
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def _kill_tree(pid: int) -> None:
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def stop(user_id: int) -> dict:
    row = _row(user_id)
    if not row:
        return status(user_id)

    if healthy(row):
        try:
            httpx.post(
                f"http://127.0.0.1:{row['port']}/api/offerbuddy/shutdown",
                headers=_headers(row),
                timeout=5,
            )
        except Exception:
            pass

    pid = row["pid"]
    deadline = time.time() + 15
    while time.time() < deadline and pid and _alive(pid):
        time.sleep(1)

    if pid and _alive(pid):
        _kill_tree(pid)

    _kill_vnc_for_port(row["port"])
    _update(user_id, status="stopped", pid=pid if pid and _alive(pid) else None)
    return status(user_id)


def _record_task_state(user_id: int, channel: str, running: bool) -> bool:
    with get_conn() as conn:
        if running:
            conn.execute(
                "UPDATE task_start_reservations SET observed_running = 1 WHERE user_id = ? AND channel = ?",
                (user_id, channel),
            )
            return False
        conn.execute(
            "DELETE FROM task_start_reservations WHERE user_id = ? AND channel = ? AND observed_running = 1",
            (user_id, channel),
        )
        return not conn.execute(
            "SELECT 1 FROM task_start_reservations WHERE user_id = ? AND channel = ? LIMIT 1",
            (user_id, channel),
        ).fetchone()


def _tasks_idle(user_id: int, row) -> bool:
    """Unknown or malformed status is never evidence that a worker is idle."""
    for channel in CHANNELS.values():
        try:
            response = httpx.get(
                f"http://127.0.0.1:{row['port']}{channel.endpoint('status')}",
                headers=_headers(row), timeout=8,
            )
            if response.status_code != 200:
                return False
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("isRunning"), bool):
                return False
        except Exception:
            return False
        if not _record_task_state(user_id, channel.key, data["isRunning"]):
            return False
    return True


def stop_if_idle(user_id: int) -> bool:
    """Reclaim only after repeat task and activity checks; retry uncertain cases."""
    row = _row(user_id)
    if not row:
        return True
    if not activity.is_idle(user_id) or not healthy(row):
        return False
    with activity.guard(user_id):
        current = _row(user_id)
        if (not current or current['pid'] != row['pid'] or current['port'] != row['port']
                or not activity.is_idle(user_id) or not healthy(current)
                or not _tasks_idle(user_id, current) or not activity.is_idle(user_id)
                or not _tasks_idle(user_id, current) or not activity.is_idle(user_id)):
            return False
        response = httpx.post(
            f"http://127.0.0.1:{current['port']}/api/offerbuddy/shutdown",
            headers=_headers(current), timeout=5,
        )
        if response.status_code != 200:
            return False
        pid = current['pid']
        if not pid:
            return False
        deadline = time.time() + 15
        while time.time() < deadline and _alive(pid):
            time.sleep(1)
        if _alive(pid):
            return False
        _kill_vnc_for_port(current['port'])
        _update(user_id, status="stopped", pid=None)
        return True


def status(user_id: int) -> dict:
    row = _row(user_id)
    if not row:
        return {"status": "stopped", "running": False}
    is_healthy = healthy(row)
    if is_healthy and row["status"] != "running":
        _update(user_id, status="running")
    if not is_healthy and row["status"] == "running":
        _update(user_id, status="stopped")
    return {
        "status": "running" if is_healthy else "stopped",
        "running": is_healthy,
        "port": row["port"],
        "dataDir": row["data_dir"],
        "pid": row["pid"],
    }


def ensure_running(user_id: int) -> dict:
    """按需启动：已运行直接返回，否则拉起并等待健康检查。"""
    row = _row(user_id)
    if row and healthy(row):
        return {"started": False, "status": status(user_id)}
    result = start(user_id)
    return {"started": True, "status": result}


def display_number(row) -> int:
    return 100 + row["port"] % 1000


def vnc_port(row) -> int:
    return VNC_PORT_START + (row["port"] - config.PORT_START)


def vnc_port_for_user(user_id: int) -> int | None:
    row = _row(user_id)
    return vnc_port(row) if row else None


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _kill_vnc_for_port(port: int) -> None:
    if WINDOWS:
        return
    try:
        subprocess.run(
            ["pkill", "-f", f"x11vnc.*-rfbport {port}"],
            capture_output=True,
        )
    except Exception:
        pass


def ensure_vnc(user_id: int) -> int:
    row = _row(user_id)
    if not row or not healthy(row):
        raise RunnerError("runner 未运行，请先启动", status_code=409)
    port = vnc_port(row)
    if _port_listening(port):
        return port
    if WINDOWS:
        raise RunnerError("本机不支持 VNC 接管（仅服务器 Xvfb 环境可用）", status_code=503)

    data_dir = Path(row["data_dir"])
    log_path = data_dir / "logs" / "x11vnc.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab")
    command = [
        "x11vnc",
        "-display", f":{display_number(row)}",
        "-rfbport", str(port),
        "-localhost",
        "-nopw",
        "-forever",
        "-shared",
        "-timeout", "600",
        "-quiet",
    ]
    try:
        subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RunnerError("服务器未安装 x11vnc", status_code=503) from exc

    deadline = time.time() + 8
    while time.time() < deadline:
        if _port_listening(port):
            return port
        time.sleep(0.2)
    raise RunnerError("x11vnc 启动失败", status_code=502, detail=_log_tail(log_path, 20))


def request(user_id: int, method: str, path: str, *, params=None, json=None, files=None, timeout: float = 60, stream: bool = False):
    if method.upper() == "GET":
        channel = next((c for c in CHANNELS.values() if path == c.endpoint("status")), None)
        if channel:
            with activity.guard(user_id):
                response = _request(user_id, method, path, params=params, json=json, files=files, timeout=timeout, stream=stream)
                if response.status_code == 200 and not stream:
                    try:
                        data = response.json()
                        if isinstance(data, dict) and isinstance(data.get("isRunning"), bool):
                            _record_task_state(user_id, channel.key, data["isRunning"])
                    except ValueError:
                        pass
                return response
        return _request(user_id, method, path, params=params, json=json, files=files, timeout=timeout, stream=stream)
    activity.begin_busy(user_id)
    try:
        with activity.guard(user_id):
            channel = next((c for c in CHANNELS.values() if path == c.endpoint("start")), None)
            reservation = None
            if channel and method.upper() == "POST":
                with get_conn() as conn:
                    reservation = conn.execute(
                        "INSERT INTO task_start_reservations(user_id, channel) VALUES (?, ?)",
                        (user_id, channel.key),
                    ).lastrowid
            response = _request(user_id, method, path, params=params, json=json, files=files, timeout=timeout, stream=stream)
            if reservation is not None and 400 <= response.status_code < 500:
                try:
                    rejected = response.json().get("success") is False
                except (ValueError, AttributeError):
                    rejected = False
                if rejected:
                    with get_conn() as conn:
                        conn.execute("DELETE FROM task_start_reservations WHERE id = ?", (reservation,))
            return response
    finally:
        activity.end_busy(user_id)


def _request(user_id: int, method: str, path: str, *, params=None, json=None, files=None, timeout: float = 60, stream: bool = False):
    row = _row(user_id)
    if not row or not healthy(row):
        raise RunnerError("runner 未运行，请先启动", status_code=409)
    url = f"http://127.0.0.1:{row['port']}{path}"
    if stream:
        return httpx.stream(method, url, headers=_headers(row), params=params, timeout=None)
    return httpx.request(
        method,
        url,
        headers=_headers(row),
        params=params,
        json=json,
        files=files,
        timeout=timeout,
    )