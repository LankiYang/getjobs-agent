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
| `docs/nowcoder-notes.md` | 牛客网申助手插件调研与借鉴点 |

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
RUNNER_HEADLESS=1              # 不建议：上游注明无头易被 Boss 检测；服务器请用 Xvfb + 有头 Chrome
RUNNER_WRAPPER=deploy/runner/run-runner.sh   # Linux 服务器：Xvfb 虚拟显示包装脚本
GETJOBS_HOME=vendor/get_jobs   # 默认就是仓库内路径
RUNNER_PORT_START=19100
```

服务器部署（Xvfb + 有头 Chrome、Docker 镜像、验证码处理）见 [docs/deployment.md](docs/deployment.md)。

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

## 接管浏览器（人机验证 / 短信验证码）

侧栏「接管浏览器」打开实时 VNC 视图（noVNC），直接操作用户 runner 里的 Chrome：

- 滑块/点选类人机验证：用鼠标直接操作
- 短信验证码：输入到顶部文本框点「输入」逐字发送；「粘贴」会写入剪贴板并发送 Ctrl+V
- 「全选」「回车」用于地址栏跳转等场景

实现：控制面为 runner 启动 x11vnc（仅监听 127.0.0.1），浏览器通过带短期签名 ticket 的 WebSocket 连接；noVNC 资源内置在 `control-plane/static/novnc`。服务器需要安装 `x11vnc`（部署文档和 Docker 镜像已包含）。

**流量**：默认压缩等级 6、画质 4，静止画面实测约 0.2 KB/s；滚动/翻页时只推送变化区域的 JPEG，不会像视频流一样持续占用。关闭接管页即断开；x11vnc 在无客户端 10 分钟后自动退出。

> 安全提示：当前 VNC 未设密码，依赖「仅本机监听 + ticket 鉴权 + 每用户 runner 隔离」。对外部署建议再加 VNC 密码、访问频率限制和审计。

## 自定义网申网站（实验）

对话里直接发一个招聘官网/网申链接（例如「打开 https://... 帮我填，姓名张三…」），agent 会：

1. `open_page` 打开页面，`page_snapshot` 读取表单元素（ref、标签、当前值、下拉选项）
2. 优先用求职画像里的信息填写，缺的字段会问你，并用 `update_profile` 记住
3. `fill_field` / `select_option` / `check_box` / `upload_resume` 完成填写，操作后重新快照核对
4. 遇到登录页、滑块、短信验证码 → 提示你点「接管浏览器」，完成后回复「继续」
5. **提交前必须经你确认**；你确认后才点击「提交/投递/申请」按钮，并核对成功页

安全边界：只允许 http/https 外网地址（拒绝内网/本机）；网页文字视为数据，忽略其中的指令；最终提交不自动执行。

限制：多步表单、iframe、强风控站点可能需要人工接管；哪些网站能自动填取决于页面结构。测试页：`/assets/apply-demo.html`、`/assets/apply-demo-login.html`（含登录+人机验证）。

## 网申站点与登录状态

- 打开过的网申站点会自动保存到侧栏「网申站点」；也可以点「＋ 添加站点」手动保存，点 × 删除
- 每个站点显示状态：**可填写**（未发现登录/验证）、**需登录/验证**、**不可达**、**未检测**
- 点击站点名字会重新打开并检测一次；列表每 30 秒刷新状态
- 登录态保存在该用户 runner 的 Chrome profile 里，同一站点下次打开通常不需要重新登录；Boss/猎聘的 Cookie 也同样持久化
- 后台任务完成通知、投递上限等能力对网申同样生效（agent 会主动汇报）

## 投递数量上限与主动通知

- 对话里说「投 3 个岗位」时，agent 会给 `start_application` 传 `max_count=3`；后台每 4 秒检查一次，达到数量后自动停止（可能因投递中任务多出 1 个）
- 投递结束、达到目标或超过 2 小时，控制面会写一条总结消息并通过 SSE 实时推送到网页；不在对话页时「对话」标签会出现红点
- 总结包含本轮新增成功数、累计状态和最近投递的公司/岗位

## 账号隔离

- 对话记录、会话、站点、投递配置都按账号存储，同一浏览器切换账号不会看到对方数据
- 登录页区分「登录 / 注册」两个模式，注册需要二次确认密码
- 会话 ID 按账号保存在浏览器本地；换浏览器或清缓存后登录，会自动恢复该账号最近一次会话

## 看板

顶部「看板」标签展示投递台账：

- KPI：总记录、已投递、已过滤、待处理、平均薪资
- 状态分布与近 14 天记录趋势
- 投递明细：公司、岗位、薪资、城市、状态、时间，岗位名可点开 Boss 原链接
- 支持按状态筛选、关键词搜索、分页

## 资料与简历

网页侧栏「资料与简历」可以配置：

- 技能介绍与 AI 打招呼提示词（用于生成开场白）
- 简历上传与预览（PDF 自动转成图片简历，或 png / jpg / gif，≤10MB）
- 默认打招呼语、AI 开关、投递时自动发送图片简历
- 黑名单公司增删

agent 工具 `get_ai_greeting_profile` / `update_ai_greeting_profile` / `get_resume_status` 可在对话中读写这些资料；图片简历需要在网页上传。

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