"""Tool-using conversational agent for per-user job applications."""

import json
import os
import uuid

import httpx

from . import runners
from .channels import CHANNELS
from .db import get_conn

PROVIDER = os.environ.get("AGENT_PROVIDER", "openai").strip().lower()
OPENAI_BASE_URL = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("AGENT_API_KEY", "")
ANTHROPIC_BASE_URL = (
    os.environ.get("AGENT_ANTHROPIC_BASE_URL")
    or os.environ.get("ANTHROPIC_BASE_URL")
    or "http://127.0.0.1:15721"
).rstrip("/")
ANTHROPIC_TOKEN = os.environ.get("AGENT_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
DEFAULT_ANTHROPIC_MODEL = "deepseek-v4.1-flash-1017"
MODEL = os.environ.get("AGENT_MODEL", "").strip() or (
    DEFAULT_ANTHROPIC_MODEL if PROVIDER == "anthropic" else ""
)
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "8"))
HISTORY_LIMIT = int(os.environ.get("AGENT_HISTORY_LIMIT", "20"))
REQUIRE_CONFIRM = os.environ.get("AGENT_REQUIRE_CONFIRM", "1") != "0"

CONFIG_FIELDS = {
    "boss": {
        "keywords",
        "cityCode",
        "salary",
        "experience",
        "degree",
        "industry",
        "jobType",
        "scale",
        "stage",
        "sayHi",
        "enableAi",
        "sendImgResume",
        "filterDeadHr",
        "allowRepeatChat",
        "debugger",
        "waitTime",
        "expectedSalaryMin",
        "expectedSalaryMax",
        "repeatChatMessages",
    },
    "liepin": {"keywords", "city", "salaryCode"},
    "51job": set(),
    "zhilian": set(),
}

MULTI_FIELDS = {"industry", "experience", "degree", "scale", "stage", "salary"}


class AgentNotConfigured(RuntimeError):
    pass


def configured() -> bool:
    if PROVIDER == "anthropic":
        return bool(ANTHROPIC_BASE_URL and MODEL)
    return bool(OPENAI_BASE_URL and MODEL)


SYSTEM_PROMPT = f"""你是一个求职投递助手，为当前登录用户服务。你可以通过工具操作他名下完全隔离的自动化投递环境。

可用渠道：boss（Boss直聘，主力，支持扫码登录）、liepin（猎聘，支持投递，登录需在浏览器内完成）。51job 和智联已停止维护，不要承诺可用。

工作流程：
1. 用户提出投递需求时，先 get_profile 和 list_channels，再 get_channel_config 了解当前条件。
2. 缺关键信息时（城市、薪资、方向），一次最多问 2-3 个问题；能从画像或已有配置推断的就不要问。
3. 把用户口语翻译成平台条件时，先调用 list_options 校验城市、薪资等取值，禁止编造。
4. 生成计划后用简洁中文展示：渠道、关键词、城市、薪资、经验/学历，以及平台每日投递上限提醒。
5. {"除非用户在最近一条消息里明确同意开始（如“开始吧”“确认投递”），否则不要调用 start_application。展示计划后先等用户确认。" if REQUIRE_CONFIRM else "用户已明确要求投递且关键信息齐全时，可以直接调用 start_application(confirm=true)。"}
6. 启动后告诉用户可以随时问进度；用户说停止时调用 stop_application。
7. 如果 status 显示未登录，调用 open_login 并把返回的二维码地址告诉用户，让他扫码；不要尝试代填验证码。
8. 不要编造投递结果，不要承诺拿到面试；提醒平台风控和每日次数限制。

工具参数里的 channel 只能是 boss 或 liepin。修改配置只传需要变化的字段。所有操作都只作用于当前用户自己的环境和账号。"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_channels",
            "description": "列出可用的投递渠道及能力",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_profile",
            "description": "读取当前用户的长期求职画像（城市、期望薪资、经验、排除公司等）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "合并更新当前用户的求职画像",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "object", "description": "要合并的画像字段"}
                },
                "required": ["patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_channel_config",
            "description": "读取指定渠道的投递配置",
            "parameters": {
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["boss", "liepin"]}},
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_options",
            "description": "查询渠道字典（城市/薪资/经验/学历/行业等），用于校验取值",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["boss", "liepin"]},
                    "option_type": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["channel", "option_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_channel_config",
            "description": "局部更新渠道配置，只改传入字段",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["boss", "liepin"]},
                    "patch": {"type": "object"},
                },
                "required": ["channel", "patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_status",
            "description": "查看渠道登录状态和任务运行状态",
            "parameters": {
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["boss", "liepin"]}},
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_application",
            "description": "启动该渠道的自动投递。confirm 必须是 true，且仅在用户明确同意后使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["boss", "liepin"]},
                    "confirm": {"type": "boolean"},
                },
                "required": ["channel", "confirm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_application",
            "description": "停止正在运行的投递任务",
            "parameters": {
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["boss", "liepin"]}},
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_results",
            "description": "查询投递结果列表",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["boss", "liepin"]},
                    "page": {"type": "integer"},
                    "size": {"type": "integer"},
                    "statuses": {"type": "string"},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats",
            "description": "获取投递统计",
            "parameters": {
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["boss", "liepin"]}},
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_login",
            "description": "打开渠道登录页并返回二维码地址，供用户扫码",
            "parameters": {
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["boss", "liepin"]}},
                "required": ["channel"],
            },
        },
    },
]


class AgentToolError(RuntimeError):
    pass


def _request_json(user_id: int, method: str, path: str, **kwargs):
    response = runners.request(user_id, method, path, **kwargs)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text[:2000]}
    if response.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else str(data)
        raise AgentToolError(f"接口返回 {response.status_code}: {message}")
    return data


def _channel(channel: str):
    entry = CHANNELS.get(channel)
    if not entry or channel not in ("boss", "liepin"):
        raise AgentToolError(f"不支持的渠道: {channel}，仅支持 boss / liepin")
    return entry


def _profile(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT data FROM agent_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["data"])
    except Exception:
        return {}


def _save_profile(user_id: int, data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_profiles(user_id, data, updated_at)
               VALUES(?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (user_id, json.dumps(data, ensure_ascii=False)),
        )


def _normalize_patch(channel: str, patch: dict) -> dict:
    allowed = CONFIG_FIELDS.get(channel, set())
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise AgentToolError(f"不支持的配置字段: {unknown}")

    body = dict(patch)
    if isinstance(body.get("keywords"), list):
        body["keywords"] = ",".join(str(item).strip() for item in body["keywords"])
    for field in MULTI_FIELDS & set(body):
        value = body[field]
        if isinstance(value, list):
            body[field] = "[" + ",".join(str(item).strip() for item in value) + "]"
    return body


def execute_tool(user_id: int, name: str, args: dict):
    if name == "list_channels":
        return {
            "channels": [
                {
                    "key": channel.key,
                    "label": channel.label,
                    "maintained": channel.maintained,
                    "loginQr": bool(channel.login_qr),
                }
                for channel in CHANNELS.values()
            ]
        }

    if name == "get_profile":
        return {"profile": _profile(user_id)}

    if name == "update_profile":
        patch = args.get("patch") or {}
        profile = _profile(user_id)
        profile.update(patch)
        _save_profile(user_id, profile)
        return {"profile": profile}

    if name == "get_channel_config":
        entry = _channel(args["channel"])
        data = _request_json(user_id, "GET", f"{entry.prefix}/config")
        return {
            "channel": entry.key,
            "config": data.get("config", {}),
            "blacklistCount": len(data.get("blacklist") or []),
        }

    if name == "list_options":
        entry = _channel(args["channel"])
        option_type = args["option_type"]
        data = _request_json(
            user_id, "GET", f"{entry.prefix}/config/options/{option_type}"
        )
        options = data if isinstance(data, list) else []
        needle = str(args.get("query") or "").strip().lower()
        matched = [
            {"name": item.get("name"), "code": item.get("code")}
            for item in options
            if not needle
            or needle in str(item.get("name") or "").lower()
            or needle in str(item.get("code") or "").lower()
        ]
        limit = int(args.get("limit") or 20)
        return {
            "channel": entry.key,
            "optionType": option_type,
            "total": len(options),
            "matched": len(matched),
            "options": matched[:limit],
        }

    if name == "update_channel_config":
        entry = _channel(args["channel"])
        body = _normalize_patch(entry.key, args.get("patch") or {})
        data = _request_json(user_id, "PUT", f"{entry.prefix}/config", json=body)
        return {"ok": True, "channel": entry.key, "saved": data.get("config", data)}

    if name == "get_status":
        entry = _channel(args["channel"])
        return _request_json(user_id, "GET", f"{entry.prefix}/status")

    if name == "start_application":
        entry = _channel(args["channel"])
        if REQUIRE_CONFIRM and not args.get("confirm"):
            return {
                "error": "需要用户明确确认后才能启动投递。请先展示计划并等待用户同意。",
                "requiresConfirmation": True,
            }
        data = _request_json(user_id, "POST", f"{entry.prefix}/start")
        return {"ok": True, "channel": entry.key, "message": data.get("message") or "已启动"}

    if name == "stop_application":
        entry = _channel(args["channel"])
        data = _request_json(user_id, "POST", f"{entry.prefix}/stop")
        return {"ok": True, "channel": entry.key, "message": data.get("message") or "已停止"}

    if name == "get_results":
        entry = _channel(args["channel"])
        params = {
            "page": int(args.get("page") or 1),
            "size": min(int(args.get("size") or 10), 20),
        }
        if args.get("statuses"):
            params["statuses"] = args["statuses"]
        return _request_json(user_id, "GET", f"{entry.prefix}/list", params=params)

    if name == "get_stats":
        entry = _channel(args["channel"])
        return _request_json(user_id, "GET", f"{entry.prefix}/stats")

    if name == "open_login":
        entry = _channel(args["channel"])
        if not entry.login_open:
            return {"error": f"{entry.label} 暂不支持接口打开登录页"}
        data = _request_json(user_id, "POST", entry.login_open)
        return {
            **data,
            "qrUrl": f"/api/channels/{entry.key}/login/qr",
            "message": data.get("message") or "已打开登录页，请扫码登录",
        }

    raise AgentToolError(f"未知工具: {name}")


def _load_history(user_id: int, session_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT role, content FROM agent_messages
               WHERE user_id = ? AND session_id = ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, session_id, HISTORY_LIMIT),
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def _save_message(user_id: int, session_id: str, role: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_messages(user_id, session_id, role, content, created_at)
               VALUES(?, ?, ?, ?, datetime('now'))""",
            (user_id, session_id, role, content),
        )


def history(user_id: int, session_id: str) -> list[dict]:
    return _load_history(user_id, session_id)


def _chat_completion(messages: list[dict]) -> dict:
    if not configured():
        raise AgentNotConfigured(
            "未配置 AGENT_BASE_URL / AGENT_MODEL，无法调用对话模型"
        )
    response = httpx.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def _anthropic_tools() -> list[dict]:
    return [
        {
            "name": tool["function"]["name"],
            "description": tool["function"]["description"],
            "input_schema": tool["function"]["parameters"],
        }
        for tool in TOOLS
    ]


def _anthropic_completion(system: str, messages: list[dict]) -> dict:
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if ANTHROPIC_TOKEN and ANTHROPIC_TOKEN != "PROXY_MANAGED":
        headers["authorization"] = f"Bearer {ANTHROPIC_TOKEN}"
    response = httpx.post(
        f"{ANTHROPIC_BASE_URL}/v1/messages",
        headers=headers,
        json={
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
            "tools": _anthropic_tools(),
            "tool_choice": {"type": "auto"},
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json()


def _anthropic_history(user_id: int, session_id: str) -> list[dict]:
    merged: list[dict] = []
    for item in _load_history(user_id, session_id):
        role = item["role"]
        content = item["content"] or ""
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n" + content
        else:
            merged.append({"role": role, "content": content})
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "（继续之前的对话）"})
    return merged


def _run_anthropic(user_id: int, session_id: str) -> dict:
    messages = _anthropic_history(user_id, session_id)
    tool_events: list[dict] = []
    qr_urls: list[str] = []

    for _ in range(MAX_STEPS):
        payload = _anthropic_completion(SYSTEM_PROMPT, messages)
        blocks = payload.get("content") or []
        text_parts = [block.get("text", "") for block in blocks if block.get("type") == "text"]
        tool_uses = [block for block in blocks if block.get("type") == "tool_use"]

        if not tool_uses:
            reply = "\n".join(text_parts).strip()
            _save_message(user_id, session_id, "assistant", reply)
            return {
                "sessionId": session_id,
                "reply": reply,
                "toolCalls": tool_events,
                "qrUrls": qr_urls,
            }

        assistant_blocks = [
            block for block in blocks if block.get("type") in ("text", "tool_use")
        ]
        messages.append({"role": "assistant", "content": assistant_blocks})

        result_blocks = []
        for block in tool_uses:
            name = block.get("name", "")
            args = block.get("input") or {}
            if not isinstance(args, dict):
                args = {}
            try:
                result = execute_tool(user_id, name, args)
            except AgentToolError as exc:
                result = {"error": str(exc)}
            except runners.RunnerError as exc:
                result = {"error": exc.message}
            except httpx.HTTPError as exc:
                result = {"error": f"后端请求失败: {exc}"}

            if isinstance(result, dict) and result.get("qrUrl"):
                qr_urls.append(result["qrUrl"])
            tool_events.append({"name": name, "arguments": args, "result": result})
            result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        messages.append({"role": "user", "content": result_blocks})

    fallback = "这轮操作步骤过多，已先暂停。请把条件再明确一下，或者告诉我下一步只做哪件事。"
    _save_message(user_id, session_id, "assistant", fallback)
    return {
        "sessionId": session_id,
        "reply": fallback,
        "toolCalls": tool_events,
        "qrUrls": qr_urls,
    }


def run_chat(user_id: int, session_id: str | None, message: str) -> dict:
    if not configured():
        raise AgentNotConfigured(
            "未配置对话模型：OpenAI 兼容请设置 AGENT_BASE_URL/AGENT_MODEL；"
            "Anthropic 兼容请设置 AGENT_PROVIDER=anthropic 和 AGENT_ANTHROPIC_BASE_URL"
        )
    if not message.strip():
        raise ValueError("消息不能为空")

    session_id = session_id or uuid.uuid4().hex
    _save_message(user_id, session_id, "user", message)

    if PROVIDER == "anthropic":
        return _run_anthropic(user_id, session_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_load_history(user_id, session_id))

    tool_events: list[dict] = []
    qr_urls: list[str] = []

    for _ in range(MAX_STEPS):
        payload = _chat_completion(messages)
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("模型没有返回候选结果")
        reply = choices[0].get("message") or {}
        tool_calls = reply.get("tool_calls") or []

        if not tool_calls:
            content = (reply.get("content") or "").strip()
            _save_message(user_id, session_id, "assistant", content)
            return {
                "sessionId": session_id,
                "reply": content,
                "toolCalls": tool_events,
                "qrUrls": qr_urls,
            }

        messages.append(
            {
                "role": "assistant",
                "content": reply.get("content"),
                "tool_calls": tool_calls,
            }
        )
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = execute_tool(user_id, name, args)
            except AgentToolError as exc:
                result = {"error": str(exc)}
            except runners.RunnerError as exc:
                result = {"error": exc.message}
            except httpx.HTTPError as exc:
                result = {"error": f"后端请求失败: {exc}"}

            if isinstance(result, dict) and result.get("qrUrl"):
                qr_urls.append(result["qrUrl"])
            tool_events.append({"name": name, "arguments": args, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    fallback = "这轮操作步骤过多，已先暂停。请把条件再明确一下，或者告诉我下一步只做哪件事。"
    _save_message(user_id, session_id, "assistant", fallback)
    return {
        "sessionId": session_id,
        "reply": fallback,
        "toolCalls": tool_events,
        "qrUrls": qr_urls,
    }