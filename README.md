# getjobs-agent

多用户、账号隔离的对话式求职投递服务：每个用户有自己的运行环境和浏览器登录态，聊天里说"帮我投大模型方向"，agent 负责补全条件、校验平台字典、执行投递并汇报结果。

支持渠道：**Boss直聘**（主力，扫码登录 + 全自动投递）、**猎聘**（投递已接通；登录需在 runner 浏览器内完成）。51job / 智联上游已停止维护。

> 当前实现为**进程级隔离**（每用户一个 runner 进程 + 独立 Chrome profile + 独立 SQLite）。容器化方案见 [docs/architecture.md](docs/architecture.md)。

## 架构

```
浏览器 UI  http://127.0.0.1:8000
        │  JWT
        ▼
控制面（FastAPI）
  ├─ 账号 / JWT / 用户画像 / 对话记忆
  ├─ Agent（LLM tool-use 循环）
  └─ RunnerManager：每用户一个进程、独立端口、独立密钥
        │  127.0.0.1 + X-Offerbuddy-Worker-Secret
        ▼
runner（get_jobs jar）
  ├─ SQLite: runners/u<id>/db/getjobs.db
  ├─ Chrome profile: runners/u<id>/browser-data
  └─ 平台 API：/api/boss/*、/api/liepin/*
        │ Playwright
        ▼
Boss直聘 / 猎聘
```

隔离边界：

| 资源 | 隔离方式 |
|---|---|
| 登录态 / 配置 / 投递记录 | 每用户独立 SQLite 文件 |
| 浏览器 Cookie / localStorage | 每用户独立 Chrome user-data-dir |
| 进程 / 端口 | 每用户独立 JVM 进程，端口从 19100 起分配 |
| API 访问 | 每用户独立随机密钥，runner 只监听 127.0.0.1 |
| 对话 / 画像 | 控制面数据库按 user_id 隔离 |

## 目录

| 目录 | 说明 |
|---|---|
| `control-plane/` | FastAPI 控制面：账号、agent、runner 管理、渠道代理、Web UI |
| `control-plane/static/` | 聊天界面（单文件，无外部依赖） |
| `mcp/` | MCP 工具层（单 runner 的本地调试/Claude Code 接入） |
| `skill/getjobs/` | Claude Code skill 编排规则 |
| `vendor/get_jobs/` | 上游源码快照（含多实例补丁，见 [docs/vendor-patches.md](docs/vendor-patches.md)） |
| `docs/architecture.md` | 多用户架构与演进路线 |

## 快速开始

### 1. 准备 runner

需要 JDK 21 和本机 Chrome。

```bash
# 空数据库（仓库不含数据库文件）
# 从 https://github.com/loks666/get_jobs/releases/latest 下载 getjobs.db.template
# 重命名为 getjobs.db 放到 vendor/get_jobs/db/

cd vendor/get_jobs
./gradlew bootJar        # 产物：build/libs/get_jobs-2.0.1.jar
```

> `vendor/get_jobs/gradle.properties` 中已注释掉上游默认的 `127.0.0.1:7897` 代理；如果本机确实跑着该代理可以恢复。

### 2. 安装依赖

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r mcp/requirements.txt -r control-plane/requirements.txt
```

### 3. 配置环境变量

```bash
# 必须
JAVA_HOME=/path/to/jdk21

# 对话模型：二选一
# A. OpenAI 兼容（BASE_URL 要带 /v1）
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://api.openai.com/v1
AGENT_API_KEY=sk-xxx
AGENT_MODEL=gpt-4o-mini

# B. Anthropic 兼容（如本机 CC Switch 代理 / 内部网关）
AGENT_PROVIDER=anthropic
AGENT_ANTHROPIC_BASE_URL=http://127.0.0.1:15721
AGENT_MODEL=deepseek-v4.1-flash-1017
# AGENT_API_KEY=...        # 代理自带鉴权时留空；不是 PROXY_MANAGED 时会作为 Bearer 发送

# 可选
AGENT_REQUIRE_CONFIRM=1        # 1=启动投递前必须用户确认；0=全自动
AGENT_MAX_TOKENS=4096
RUNNER_HEADLESS=1              # 1=无头运行 Chrome，不弹窗口（Boss 风控概率略高）
GETJOBS_HOME=vendor/get_jobs   # 默认就是仓库内路径
RUNNER_PORT_START=19100
```

### 4. 启动控制面

```bash
cd control-plane
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 ，注册账号 → 启动运行环境 → Boss 扫码登录 → 开始对话。

### 5. 冒烟测试

```bash
# MCP 工具层
.venv/Scripts/python.exe mcp/test_mcp_client.py

# agent 循环（无需真实 LLM：启动 tests/mock_llm.py 并设置 AGENT_BASE_URL=http://127.0.0.1:9999/v1）
.venv/Scripts/python.exe control-plane/tests/mock_llm.py
```

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` / `/api/auth/login` | 注册 / 登录，返回 JWT |
| GET | `/api/me` | 当前用户与 runner 状态 |
| POST | `/api/me/runner/start` / `stop` | 启动 / 停止自己的 runner |
| POST | `/api/agent/chat` | 对话（agent 自动调用工具） |
| GET | `/api/agent/history?session_id=` | 对话记录 |
| GET | `/api/channels` | 渠道列表 |
| GET/PUT | `/api/channels/{ch}/config` | 渠道配置 |
| GET | `/api/channels/{ch}/config/options/{type}` | 平台字典 |
| POST | `/api/channels/{ch}/start` / `stop` / `logout` | 投递控制 |
| GET | `/api/channels/{ch}/status` / `stats` / `list` | 状态与结果 |
| POST | `/api/channels/{ch}/login` | 打开登录页 |
| GET | `/api/channels/{ch}/login/qr` | 登录二维码（PNG） |
| GET | `/api/channels/{ch}/stream` | 进度 SSE |

除 `/api/auth/*` 和 `/api/health` 外都需要 `Authorization: Bearer <token>`。

## agent 的工作方式

1. 读用户画像与当前配置
2. 把口语翻译成平台条件，先用平台字典校验（城市、薪资区间等）
3. 展示计划，等待确认（`AGENT_REQUIRE_CONFIRM=1` 时）
4. 写配置 → 启动投递 → 返回结果；用户可随时问进度或停止
5. 未登录时返回二维码地址，由用户在聊天界面扫码

## 本地 MCP（可选）

`mcp/` 是给 Claude Code 单 runner 调试用的工具层，`skill/getjobs/` 是配套编排规则；服务端 agent 不依赖它们。

## 限制与后续

- 猎聘登录暂未做二维码中转，需要在 runner 浏览器窗口内完成（或通过上层自行扩展 `/api/liepin/save-cookie`）
- 51job / 智联上游停止维护，仅保留渠道占位
- 单机进程模型，控制面重启会重启 runner；容器化、队列、配额、TLS 见 roadmap
- Boss 每日打招呼有上限（约 150 次），平台风控与账号风险需自行承担

## 许可与合规

- `vendor/get_jobs/` 版权归上游作者 loks666，采用 PolyForm Noncommercial 1.0.0，仅允许非商业使用；商业运营需获得作者授权
- 本仓库不包含 API Key、Cookie、简历或登录态
- 自动投递可能违反招聘平台用户协议，大规模代投的风险由运营方承担