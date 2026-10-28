"""Control plane configuration."""

import os
import secrets
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


DEFAULT_GETJOBS_HOME = Path(
    os.environ.get("GETJOBS_HOME", str(REPO_ROOT / "vendor" / "get_jobs"))
)
CONTROL_DB = _env_path("CONTROL_DB", BASE_DIR / "control.db")
JWT_SECRET_FILE = _env_path("CONTROL_JWT_SECRET_FILE", BASE_DIR / "jwt.secret")
JWT_TTL_HOURS = int(os.environ.get("CONTROL_JWT_TTL_HOURS", "168"))
RUNNERS_ROOT = _env_path("RUNNERS_ROOT", REPO_ROOT / "runners")
GETJOBS_JAR = _env_path("GETJOBS_JAR", DEFAULT_GETJOBS_HOME / "build/libs/get_jobs-2.0.1.jar")
PATCHRIGHT_DIR = _env_path("PATCHRIGHT_DIR", DEFAULT_GETJOBS_HOME / "build/patchright-driver")
DB_TEMPLATE = _env_path("GETJOBS_DB_TEMPLATE", DEFAULT_GETJOBS_HOME / "db/getjobs.db")
PORT_START = int(os.environ.get("RUNNER_PORT_START", "19100"))
PORT_END = int(os.environ.get("RUNNER_PORT_END", "19200"))
START_TIMEOUT = float(os.environ.get("RUNNER_START_TIMEOUT", "120"))
ALLOWED_ORIGINS = [item.strip() for item in os.environ.get("CONTROL_ALLOWED_ORIGINS", "*").split(",")]


def java_bin() -> str:
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("java")
    if not found:
        raise RuntimeError("java not found; set JAVA_HOME or add java to PATH")
    return found


def node_bin() -> str | None:
    return os.environ.get("PLAYWRIGHT_NODEJS_PATH") or shutil.which("node")


def jwt_secret() -> str:
    from_env = os.environ.get("CONTROL_JWT_SECRET")
    if from_env:
        return from_env
    if JWT_SECRET_FILE.is_file():
        return JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    JWT_SECRET_FILE.write_text(value, encoding="utf-8")
    return value