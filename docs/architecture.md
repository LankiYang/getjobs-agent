# 多用户服务化架构

目标：多用户、账号隔离、全自动的求职投递服务，支持多渠道。

## 已实现（P1，进程级隔离）

```
用户 A/B/C ──浏览器──> 控制面 (FastAPI :8000)
                         ├─ 账号 / JWT（PBKDF2 + HS256）
                         ├─ Agent（LLM tool-use 循环，工具见下）
                         ├─ 渠道代理（boss / liepin / 51job / zhilian 占位）
                         └─ RunnerManager
                               │ 每用户：独立进程 / 端口 / 密钥 / 目录
                               ▼
                    runner-A (get_jobs jar)      runner-B ...
                    ├─ 127.0.0.1:1910x
                    ├─ db/getjobs.db（独立）
                    ├─ browser-data/（独立 Chrome profile）
                    └─ 按需拉起 Chrome
```

### 隔离矩阵

| 资源 | 实现 |
|---|---|
| 配置、Cookie、投递记录 | `runners/u<id>/db/getjobs.db` |
| 浏览器登录态 | `runners/u<id>/browser-data/` |
| 进程与端口 | 每用户一个 JVM，19100 起分配，仅监听 127.0.0.1 |
| runner API | 每用户随机 `OB_WORKER_SECRET`，控制面按用户注入请求头 |
| 简历图片 | runner 内 `src/main/resources/resume*`（运行时上传） |
| 对话与画像 | 控制面 SQLite，按 user_id 隔离 |

### Agent 工具

`list_channels`、`get_profile` / `update_profile`、`get_channel_config`、`list_options`、`update_channel_config`、`get_status`、`start_application`、`stop_application`、`get_results`、`get_stats`、`open_login`。

护栏：

- `AGENT_REQUIRE_CONFIRM=1`（默认）时，未获用户明确同意不能启动投递
- 渠道参数、配置字段白名单校验；城市/薪资等必须来自平台字典
- 未登录时只返回二维码，不代登录、不碰验证码

### 登录与二维码

控制面 `POST /api/channels/boss/login` → runner 懒启动 Chrome 并打开登录页；`GET /api/channels/boss/login/qr` 返回二维码 PNG；前端用带 JWT 的 fetch 取回并展示。登录态只落在该用户自己的 `browser-data`。

### 生命周期

- 用户注册后手动启动 runner（或将来自动拉起）
- runner 作为控制面子进程启动；当前开发环境下停止控制面会一并停止 runner（生产部署应使用容器/独立服务管理器，让 runner 生命周期独立于控制面）
- 停止：优先调用 `/api/offerbuddy/shutdown` 优雅退出，超时后 `taskkill /T /F`
- 空闲回收尚未实现（roadmap）

### 安全现状

- 控制面默认只监听 127.0.0.1；对外部署必须置于反向代理 + TLS 之后
- runner 只监听 127.0.0.1，公网不可达
- 每 runner 独立密钥校验；请求不得带 Origin
- JWT 密钥首次运行生成到 `control-plane/jwt.secret`
- 已知待办：登录限流、密码找回、审计日志、密钥轮换

## 下一阶段

1. **容器化**：Dockerfile（JDK 21 + Chrome + Patchright）、卷挂载、健康检查，替换进程模型
2. **调度与配额**：全局任务队列、每账号每日投递上限、空闲回收、自动扩缩容
3. **可观测性**：投递成功率、登录态过期告警、runner 日志聚合
4. **多渠道补齐**：猎聘二维码中转、51job/智联适配或下线
5. **计费与授权**：套餐、用量统计、上游商业授权

## 容量估算

| 项目 | 值 |
|---|---|
| 单 runner（JVM 空闲） | ~200–300 MB |
| 单 runner（Boss 活跃 + Chrome） | ~0.8–1.2 GB |
| 16 GB 机器活跃用户 | 8–12（仅 Boss） |
| Boss 单账号投递上限 | ~150 次打招呼/天 |

## 风险

- **许可**：上游 PolyForm Noncommercial 1.0.0，商业运营需作者授权
- **平台风控**：多租户代投属于平台协议禁止的规模化行为；出口 IP 需按用户/批次隔离
- **合规建议**：可考虑"服务只做编排与建议、投递在用户本机执行"的轻量模式降低集中风险