# 服务器部署

服务器部署使用 **"有头 Chrome + Xvfb 虚拟显示"**：不对桌面弹窗（服务器本来也没有桌面），同时避免无头模式被 Boss 风控识别。不要设置 `RUNNER_HEADLESS`。

新账号的 `db/getjobs.db` 从 `GETJOBS_DB_TEMPLATE` 复制。控制面在复制后会执行 `runners._clear_template_bio`，清空模板自带的 `ai.introduce`（上游作者的个人技术栈示例）；否则新用户会带着别人的简历描述去生成打招呼语。替换模板文件时不需要再手工清理这一段。

## 方式 A：单机进程模式（先跑通）

依赖：OpenJDK 21、Google Chrome stable、Xvfb、Node.js 18+、中文字体（Noto CJK）。

```bash
# 1. 构建 runner
cd vendor/get_jobs
./gradlew bootJar          # 产物 build/libs/get_jobs-2.0.1.jar + build/patchright-driver

# 2. 启动控制面
cd ../..
export JAVA_HOME=/usr/lib/jvm/temurin-21
export GETJOBS_HOME=$PWD/vendor/get_jobs
export PLAYWRIGHT_CLI_DIR=$PWD/vendor/get_jobs/build/patchright-driver
export RUNNER_WRAPPER=$PWD/deploy/runner/run-runner.sh
export AGENT_PROVIDER=anthropic
export AGENT_ANTHROPIC_BASE_URL=http://127.0.0.1:15721
export AGENT_MODEL=deepseek-v4.1-flash-1017
cd control-plane
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

每个用户一个 runner 进程：独立端口、独立数据目录、独立 Chrome profile；`run-runner.sh` 为每个 runner 拉起独立的 Xvfb display（`:100 + port % 1000`），Chrome 以非无头模式渲染在虚拟屏幕上。

## 方式 B：每用户一个容器（推荐对外服务）

```bash
cd vendor/get_jobs && ./gradlew bootJar && cd ../..
docker build -f deploy/runner/Dockerfile -t getjobs-runner:latest .

docker run -d --name runner-u1 -p 127.0.0.1:19100:19100 \
  -v /srv/getjobs/u1:/data \
  -e OB_WORKER_SECRET='至少32位随机串' \
  getjobs-runner:latest 19100 /data /opt/getjobs/get_jobs.jar
```

镜像内置：JDK 21、Google Chrome stable、Xvfb、Node 20、中文字体、patchright driver。

控制面默认用本地进程拉起 runner；容器模式下可以把 `RUNNER_WRAPPER` 指向一个调用 `docker run` 的包装脚本（参数为 port/data-dir/jar），或后续接入容器编排。

## 登录与验证码

- 二维码：`GET /api/channels/boss/login/qr` 返回 PNG，在网页/聊天里展示，用户扫码即可
- 人机验证 / 短信验证码：网页「接管浏览器」通过 noVNC 实时操作 runner 的 Chrome（控制面自动启动 x11vnc，仅监听 127.0.0.1，使用短期签名 ticket 鉴权）
- 需要 x11vnc：`apt install x11vnc`（Docker 镜像已内置）；生产环境建议补 VNC 密码与频率限制

## 安全

- 控制面不要直接暴露公网，放在 HTTPS 反向代理之后
- runner 只监听 127.0.0.1（容器内网），使用每用户随机 `OB_WORKER_SECRET`
- 数据卷包含登录态和简历：按用户加密备份、最小权限
- `control-plane/control.db`、`jwt.secret`、`runners/` 不要提交或公开

## 容量估算

| 状态 | 内存 |
|---|---|
| runner 空闲（JVM） | 200–300 MB |
| runner 活跃（Chrome + 投递） | 0.8–1.2 GB |
| 16 GB 机器活跃用户 | 8–12（仅 Boss） |

控制面在启动新 runner 前执行容量准入；对约 2 GiB、与其他服务共用的机器，默认最多 **1** 个存活 runner，且宿主机可用物理内存至少 **1024 MiB**。在控制面启动前设置环境变量：

- `RUNNER_MAX_CONCURRENT`：正整数，默认 `1`。包含正在启动或仍存活但暂时不健康的进程，不以数据库状态文字为准；确认死亡的旧记录不占名额。
- `RUNNER_MIN_AVAILABLE_MIB`：非负整数，默认 `1024`。Linux 使用 `/proc/meminfo` 的 `MemAvailable`，Windows 使用系统可用物理内存；设为 `0` 可显式关闭内存检查。开启时无法读取内存也会拒绝新启动。

达到上限、可用内存不足或无法检查时，新启动返回 HTTP 503；已健康运行的 runner 不受影响。准入不会为了腾容量终止任务或其他用户进程。启动检查在共享同一个 `CONTROL_DB` 的控制面进程之间串行化，锁文件位于数据库旁边（`<数据库文件名>.runner-admission`），目录必须可写；各实例须使用相同限制值，并使用可靠的本地文件系统。独立数据库、手工启动的 runner 或独立容器不受此上限协调。

内存门槛只检查启动瞬间的宿主机可用内存，不是内存预留或硬限制，也不考虑 cgroup 容器限额；浏览器与任务之后仍会增长，其他服务也会消耗内存，因此不能保证不会 OOM。容器部署应另行设置并监控 cgroup 内存预算，并按实际峰值调整上限。控制面在进程启动与记录 PID 之间异常退出时，可能需要人工检查并清理孤儿进程；PID 被系统复用也可能导致保守拒绝。

## 常见问题

- **Chrome 起不来**：确认装了 Google Chrome stable（不是 Chromium），并给容器加 `--shm-size=1g`
- **中文字体缺失**：安装 `fonts-noto-cjk`，否则页面乱码/元素错位
- **无头检测**：遇到风控时不要切无头，先用 VNC 人工过验证码
- **Xvfb 冲突**：不同 runner 端口不同即 display 不同；手动指定可用 `XVFB_DISPLAY`