# getjobs-agent

对话式求职投递 agent：用户用自然语言提出投递需求（"帮我投大模型方向"），agent 读配置、查平台字典、生成投递计划、确认后驱动 [get_jobs](https://github.com/loks666/get_jobs) 完成 Boss 直聘自动投递，并汇报结果。

当前为 **P0 本地验证版**：MCP 工具层 + Claude Code skill 编排，目标架构（多用户服务化）见 [docs/architecture.md](docs/architecture.md)。

## 架构

```
用户聊天（Claude Code / 自研前端）
        ↓
Agent（LLM tool-use 循环）
        ↓ MCP stdio
getjobs-mcp（本仓库 mcp/）
        ↓ HTTP
get_jobs 后端 :9527（vendor/get_jobs）
        ↓ Playwright
Chrome → Boss 直聘
```

## 仓库结构

| 目录 | 说明 |
|---|---|
| `mcp/` | MCP server（13 个工具）与冒烟测试 |
| `skill/getjobs/` | Claude Code skill：扩词、确认、播报、护栏等编排规则 |
| `vendor/get_jobs/` | 上游 get_jobs 源码快照（PolyForm Noncommercial，见下方许可说明） |
| `docs/architecture.md` | 多用户服务化架构设计 |

## 快速开始

### 1. 启动 get_jobs 后端

需要 JDK 21。以 vendor 内源码为例：

```bash
cd vendor/get_jobs
# 首次：从 release 下载空库 getjobs.db.template，重命名为 getjobs.db 放到 db/
# https://github.com/loks666/get_jobs/releases/latest
./gradlew bootRun
```

后端启动后：

- 管理页面：http://localhost:6866
- API：http://localhost:9527
- 首次使用需在自动拉起的 Chrome 窗口里扫码登录 Boss

> 注意：`vendor/get_jobs/gradle.properties` 中已注释掉上游默认的 `127.0.0.1:7897` 代理配置，没有该代理的机器可以直接构建；如果你本机确实跑着 7897 代理，可以自行恢复。

### 2. 安装 MCP 依赖

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r mcp/requirements.txt
```

（macOS/Linux 把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`。）

### 3. 注册 MCP

复制 `.mcp.json.example` 为 `.mcp.json`（项目根目录），把其中绝对路径改成你的实际路径。

Claude Code 中重新打开会话，用 `/mcp` 确认 `getjobs` 已连接。

### 4. 安装 skill（可选，用于自动编排）

```bash
mkdir -p .claude/skills/getjobs
cp skill/getjobs/SKILL.md .claude/skills/getjobs/SKILL.md
```

### 5. 冒烟测试

```bash
.venv/Scripts/python.exe mcp/test_mcp_client.py
```

### 6. 开始使用

在 Claude Code 里直接说：

> 帮我投大模型方向的岗位，城市上海，薪资 20-30K

agent 会读配置 → 校验计划 → 给你确认 → 启动投递 → 轮询进度 → 汇报结果。

## 工具清单

| 工具 | 作用 |
|---|---|
| `get_boss_config` | 读取当前投递配置 |
| `list_boss_options` | 查城市/薪资/经验/学历等平台字典 |
| `validate_plan` | 校验并翻译投递计划（不写配置） |
| `update_boss_config` | 局部更新配置，未传字段保持不变 |
| `get_boss_status` | 登录状态、任务状态 |
| `start_boss_delivery` | 启动投递（必须 `confirm=true`） |
| `stop_boss_delivery` | 停止投递 |
| `get_boss_results` | 投递结果分页查询 |
| `get_boss_stats` | 投递统计 |
| `list_boss_blacklist` / `add_boss_blacklist` | 黑名单管理 |
| `get_profile` / `update_profile` | 长期求职画像（本地 `profile.json`） |

## 关键设计

- **计划先确认**：`start_boss_delivery` 未带 `confirm=true` 直接拒绝；首次投递必须人工确认
- **参数强校验**：城市/薪资等一律走平台字典，薪资支持口语映射（"20-30K" → 平台区间 "20-50K"）
- **局部更新**：`update_boss_config` 只提交传入字段，避免全量覆盖清空打招呼语等配置
- **调试模式**：`update_boss_config({"debugger": 1})` 只遍历岗位、不真实投递

## Roadmap

- [x] P0：MCP 工具层 + skill，本地跑通 Boss 单平台
- [ ] P1：流式聊天界面、计划确认卡片、进度播报
- [ ] P2：用户画像与偏好记忆，根据结果迭代关键词
- [ ] P3：多用户服务化（控制面 + 每用户 runner 容器），见 `docs/architecture.md`

## 许可与合规

- `vendor/get_jobs/` 版权归上游作者 loks666，采用 **PolyForm Noncommercial 1.0.0**，仅允许非商业使用；商业使用需联系上游作者获得授权
- 本仓库不包含任何 API Key、Cookie、简历或登录态；使用时不要提交 `profile.json`、`.env`、`boss-login-qr.png` 等本地文件
- 自动投递自动化可能违反招聘平台用户协议，账号风控风险由使用者自行承担