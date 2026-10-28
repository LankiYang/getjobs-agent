# 服务器部署

服务器部署使用 **"有头 Chrome + Xvfb 虚拟显示"**：不对桌面弹窗（服务器本来也没有桌面），同时避免无头模式被 Boss 风控识别。不要设置 `RUNNER_HEADLESS`。

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
- 需要人工过滑块/验证码时：容器内已装 `x11vnc`，可通过受限内网或 VPN 临时连接虚拟屏幕操作

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

## 常见问题

- **Chrome 起不来**：确认装了 Google Chrome stable（不是 Chromium），并给容器加 `--shm-size=1g`
- **中文字体缺失**：安装 `fonts-noto-cjk`，否则页面乱码/元素错位
- **无头检测**：遇到风控时不要切无头，先用 VNC 人工过验证码
- **Xvfb 冲突**：不同 runner 端口不同即 display 不同；手动指定可用 `XVFB_DISPLAY`