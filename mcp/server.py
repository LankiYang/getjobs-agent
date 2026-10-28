"""get_jobs MCP server: exposes Boss delivery capabilities as MCP tools."""

import json
import os
import re
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("GETJOBS_BASE_URL", "http://localhost:9527").rstrip("/")
TIMEOUT = float(os.environ.get("GETJOBS_TIMEOUT", "30"))
PROFILE_PATH = Path(
    os.environ.get("GETJOBS_PROFILE", str(Path(__file__).with_name("profile.json")))
)

mcp = FastMCP("getjobs")

OPTION_TYPES = ("city", "industry", "experience", "jobType", "salary", "degree", "scale", "stage")

CONFIG_FIELDS = {
    "debugger",
    "waitTime",
    "keywords",
    "cityCode",
    "industry",
    "jobType",
    "experience",
    "degree",
    "salary",
    "scale",
    "stage",
    "sayHi",
    "expectedSalaryMin",
    "expectedSalaryMax",
    "enableAi",
    "sendImgResume",
    "allowRepeatChat",
    "repeatChatMessages",
    "filterDeadHr",
    "deadStatus",
}

MULTI_VALUE_FIELDS = ("industry", "experience", "degree", "scale", "stage", "salary")

_option_cache: dict[str, list[dict]] = {}


def _call(method: str, path: str, **kwargs) -> dict:
    try:
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
            response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "error": f"无法连接 get_jobs 后端 ({BASE_URL}): {exc}。请确认后端已启动。",
        }

    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:800]
        return {"ok": False, "status": response.status_code, "error": detail}

    try:
        return {"ok": True, "data": response.json()}
    except Exception:
        return {"ok": True, "data": response.text}


def _options(option_type: str) -> list[dict]:
    if option_type not in _option_cache:
        result = _call("GET", f"/api/boss/config/options/{option_type}")
        if not result["ok"]:
            _option_cache[option_type] = []
        else:
            _option_cache[option_type] = result["data"] or []
    return _option_cache[option_type]


def _resolve_option(option_type: str, value: str) -> tuple[dict | None, str | None]:
    options = _options(option_type)
    if not options:
        return None, f"{option_type} 字典加载失败，请确认后端已启动且数据库已初始化"

    needle = str(value).strip()
    exact = [o for o in options if o.get("name") == needle or o.get("code") == needle]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"'{needle}' 匹配到多个同名项，请换一个更精确的名称"

    lowered = needle.lower()
    fuzzy = [
        o
        for o in options
        if lowered in str(o.get("name") or "").lower()
        or lowered in str(o.get("code") or "").lower()
    ]
    if len(fuzzy) == 1:
        return fuzzy[0], None
    if len(fuzzy) > 1:
        candidates = [o.get("name") for o in fuzzy[:8]]
        return None, f"'{needle}' 有多个候选: {candidates}"

    sample = [o.get("name") for o in options[:10]]
    return None, f"'{needle}' 不在 {option_type} 字典中，可选示例: {sample}"


def _parse_range(text: str) -> tuple[int, int] | None:
    numbers = [int(n) for n in re.findall(r"\d+", str(text))]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def _option_range(name: str) -> tuple[int, int] | None:
    name = str(name)
    if "不限" in name:
        return 0, 10**9
    numbers = [int(n) for n in re.findall(r"\d+", name)]
    if not numbers:
        return None
    if "以下" in name:
        return 0, numbers[0]
    if "以上" in name:
        return numbers[0], 10**9
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def _resolve_salary(value: str) -> tuple[dict | None, str | None]:
    options = _options("salary")
    if not options:
        return None, "salary 字典加载失败，请确认后端已启动"

    needle = str(value).strip()
    for option in options:
        if option.get("name") == needle or option.get("code") == needle:
            return option, None

    user_range = _parse_range(needle)
    if user_range is None:
        sample = [o.get("name") for o in options[:8]]
        return None, f"无法解析薪资 '{needle}'，可选示例: {sample}"

    low, high = user_range
    containing = []
    overlapping = []
    for option in options:
        option_range = _option_range(option.get("name"))
        if not option_range:
            continue
        opt_low, opt_high = option_range
        if opt_low <= low and opt_high >= high:
            containing.append((opt_high - opt_low, option))
        if opt_low <= high and opt_high >= low:
            overlapping.append((opt_high - opt_low, option))

    pool = containing or overlapping
    if pool:
        pool.sort(key=lambda item: item[0])
        return pool[0][1], None

    sample = [o.get("name") for o in options[:8]]
    return None, f"薪资 '{needle}' 没有匹配的区间，可选示例: {sample}"


def _split_values(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in re.split(r"[,，]", str(value)) if v.strip()]


def _read_profile() -> dict:
    if PROFILE_PATH.is_file():
        try:
            return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


@mcp.tool()
def get_boss_config() -> dict:
    """读取 Boss 直聘当前的投递配置：关键词、城市、薪资、经验、学历、打招呼语、AI/图片简历开关等，以及黑名单数量和简历图状态。修改配置前先调用它。"""
    result = _call("GET", "/api/boss/config")
    if not result["ok"]:
        return result

    data = result["data"] or {}
    config = data.get("config") or {}
    blacklist = data.get("blacklist") or []
    return {
        "ok": True,
        "config": config,
        "blacklistCount": len(blacklist),
        "blacklistSample": [b.get("value") for b in blacklist[:10]],
        "resumeImage": data.get("resumeImage"),
    }


@mcp.tool()
def list_boss_options(option_type: str, query: str = "", limit: int = 30) -> dict:
    """查询平台字典选项。option_type 取 city/industry/experience/jobType/salary/degree/scale/stage；query 按名称或代码模糊过滤；limit 控制返回条数。用于把用户口语（如"上海""25K左右"）翻译成平台可接受的取值。"""
    option_type = (option_type or "").strip()
    if option_type not in OPTION_TYPES:
        return {"ok": False, "error": f"不支持的 option_type: {option_type}", "allowed": list(OPTION_TYPES)}

    options = _options(option_type)
    if not options:
        return {"ok": False, "error": f"{option_type} 字典加载失败，请确认后端已启动"}

    needle = (query or "").strip().lower()
    matched = [
        o
        for o in options
        if not needle
        or needle in str(o.get("name") or "").lower()
        or needle in str(o.get("code") or "").lower()
    ]
    limit = max(1, min(int(limit), 200))
    return {
        "ok": True,
        "type": option_type,
        "total": len(options),
        "matched": len(matched),
        "options": [{"name": o.get("name"), "code": o.get("code")} for o in matched[:limit]],
    }


@mcp.tool()
def validate_plan(plan: dict) -> dict:
    """校验一份投递计划并翻译成平台取值，不修改任何配置。plan 示例: {"keywords": ["大模型应用开发", "LLM"], "city": "上海", "salary": ["20-30K"], "experience": "3-5年", "degree": "本科"}。返回 resolved（可直接传给 update_boss_config）、errors 和 warnings。errors 非空时必须先向用户澄清。"""
    plan = plan or {}
    resolved: dict = {}
    errors: list[str] = []
    warnings: list[str] = []

    keywords = plan.get("keywords")
    if keywords:
        items = _split_values(keywords)
        if not items:
            errors.append("keywords 为空")
        if len(items) > 8:
            warnings.append(f"关键词有 {len(items)} 个，建议不超过 8 个，避免单轮耗时过长")
        if any(len(k) > 20 for k in items):
            errors.append("单个关键词长度不能超过 20 个字符")
        resolved["keywords"] = items
    else:
        warnings.append("未提供 keywords，将沿用当前配置中的关键词")

    city = plan.get("city") or plan.get("cityCode")
    if city:
        option, error = _resolve_option("city", str(city).split(",")[0].strip())
        if error:
            errors.append(f"城市: {error}")
        else:
            resolved["cityCode"] = option.get("name")

    for field in ("salary", "experience", "degree", "industry", "jobType", "scale", "stage"):
        value = plan.get(field)
        if not value:
            continue
        names = []
        for item in _split_values(value):
            if field == "salary":
                option, error = _resolve_salary(item)
            else:
                option, error = _resolve_option(field, item)
            if error:
                errors.append(f"{field}: {error}")
            else:
                names.append(option.get("name"))
        if not names:
            continue
        if field == "jobType":
            resolved[field] = names[0]
        else:
            resolved[field] = names if len(names) > 1 else names[0]

    return {
        "ok": not errors,
        "resolved": resolved,
        "errors": errors,
        "warnings": warnings,
        "next": "errors 为空时，把 resolved 作为 patch 传给 update_boss_config" if not errors else "先向用户澄清 errors",
    }


@mcp.tool()
def update_boss_config(patch: dict) -> dict:
    """更新 Boss 投递配置，只传需要修改的字段，未传字段保持原值。常用字段：keywords（列表或逗号串）、cityCode、salary、experience、degree、industry、jobType、sayHi、enableAi、sendImgResume、filterDeadHr、allowRepeatChat、debugger（1=只遍历不投递）。建议先用 validate_plan 校验。"""
    patch = patch or {}
    unknown = sorted(set(patch) - CONFIG_FIELDS)
    if unknown:
        return {"ok": False, "error": f"不支持修改的字段: {unknown}", "allowed": sorted(CONFIG_FIELDS)}

    body = dict(patch)
    if "keywords" in body and isinstance(body["keywords"], list):
        body["keywords"] = ",".join(str(k).strip() for k in body["keywords"] if str(k).strip())

    for field in MULTI_VALUE_FIELDS:
        value = body.get(field)
        if isinstance(value, list):
            body[field] = "[" + ",".join(str(v).strip() for v in value) + "]"

    result = _call("PUT", "/api/boss/config", json=body)
    if not result["ok"]:
        return result
    return {
        "ok": True,
        "saved": result["data"],
        "note": "未传入的字段保持原值；keywords/城市/薪资等已由后端标准化",
    }


@mcp.tool()
def get_boss_status() -> dict:
    """读取 Boss 任务状态：是否已登录、任务是否运行中、浏览器是否就绪、本次已完成任务数。启动投递前用它确认登录状态。"""
    return _call("GET", "/api/boss/status")


@mcp.tool()
def start_boss_delivery(confirm: bool = False) -> dict:
    """启动 Boss 自动投递。必须先向用户展示投递计划（关键词、城市、薪资、预计数量）并得到明确同意，再以 confirm=True 调用。confirm=False 会被拒绝。"""
    if not confirm:
        return {
            "ok": False,
            "error": "尚未获得用户确认。请先把计划展示给用户，得到明确同意后再以 confirm=True 调用。",
        }

    status = _call("GET", "/api/boss/status")
    if status["ok"]:
        data = status["data"] or {}
        if not data.get("isLoggedIn"):
            return {
                "ok": False,
                "error": "Boss 尚未登录。请让用户在自动化浏览器窗口扫码登录后再试。",
                "status": data,
            }
        if data.get("isRunning"):
            return {"ok": False, "error": "已有投递任务在运行中，先停止或等待结束。", "status": data}

    result = _call("POST", "/api/boss/start")
    if not result["ok"]:
        return result
    return {"ok": True, "message": "Boss 投递任务已启动", "response": result["data"]}


@mcp.tool()
def stop_boss_delivery() -> dict:
    """停止正在运行的 Boss 投递任务。"""
    return _call("POST", "/api/boss/stop")


@mcp.tool()
def get_boss_results(page: int = 1, size: int = 20, statuses: str = "", keyword: str = "") -> dict:
    """查询投递结果列表（分页）。statuses 可按投递状态过滤（逗号分隔），keyword 按岗位/公司名模糊过滤。"""
    params = {"page": max(1, int(page)), "size": max(1, min(int(size), 100))}
    if statuses:
        params["statuses"] = statuses
    if keyword:
        params["keyword"] = keyword
    return _call("GET", "/api/boss/list", params=params)


@mcp.tool()
def get_boss_stats() -> dict:
    """获取投递统计（总数、各状态分布、薪资/经验/学历维度统计），用于汇报本轮结果。"""
    return _call("GET", "/api/boss/stats")


@mcp.tool()
def list_boss_blacklist() -> dict:
    """列出 Boss 黑名单公司。"""
    result = _call("GET", "/api/boss/config/blacklist")
    if not result["ok"]:
        return result
    items = result["data"] or []
    return {"ok": True, "count": len(items), "items": items[:200]}


@mcp.tool()
def add_boss_blacklist(company: str) -> dict:
    """把公司加入黑名单，后续投递会自动跳过。company 为公司名称。"""
    company = (company or "").strip()
    if len(company) < 2:
        return {"ok": False, "error": "公司名称太短，至少 2 个字符"}
    return _call("POST", "/api/boss/config/blacklist", json={"type": "company", "value": company})


@mcp.tool()
def get_profile() -> dict:
    """读取长期保存的求职者画像（城市、期望薪资、经验、学历、排除公司、打招呼偏好等）。开始规划前先读。"""
    return {"ok": True, "profile": _read_profile(), "path": str(PROFILE_PATH)}


@mcp.tool()
def update_profile(patch: dict) -> dict:
    """合并更新求职者画像，只覆盖传入字段。用于记住用户长期偏好，例如 {"city": "上海", "expectedSalary": "20-30K", "excludeCompanies": ["外包"]}。"""
    patch = patch or {}
    profile = _read_profile()
    profile.update(patch)
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "profile": profile, "path": str(PROFILE_PATH)}


if __name__ == "__main__":
    mcp.run()