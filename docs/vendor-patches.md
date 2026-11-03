# vendor/get_jobs 补丁说明

`vendor/get_jobs/` 是上游 [loks666/get_jobs](https://github.com/loks666/get_jobs) 的快照（2026-10）。为支持多用户 runner 模式，做了以下最小修改；升级上游时需要重新应用。

## 1. OfferbuddyWorkerFilter：放开 worker 模式 API

文件：`src/main/java/com/getjobs/application/config/OfferbuddyWorkerFilter.java`

原逻辑在设置 `OB_WORKER_SECRET` 后只允许少数精确路径，控制面需要的字典、统计、黑名单、SSE 等接口会被 403。

修改为前缀白名单：

```java
ALLOWED_PREFIXES = [
  "/api/offerbuddy/", "/api/boss/", "/api/liepin/", "/api/51job/", "/api/jobs/",
  "/api/zhilian/", "/api/ai/", "/api/config", "/api/cookie", "/api/health", "/api/playwright/"
]
```

安全边界不变：仍需 `OB_WORKER_SECRET`（≥32 字符）、请求头 `X-Offerbuddy-Worker-Secret` 完全匹配、且不允许带 `Origin` 头；runner 只监听 `127.0.0.1`。

## 2. gradle.properties：注释本机代理

文件：`gradle.properties`

上游把作者本机的 Clash 代理（`127.0.0.1:7897`）写进了 `systemProp`，没有该代理的机器无法解析任何依赖。快照中已注释，构建直连。

## 3. PlaywrightManager：支持无头模式

文件：`src/main/java/com/getjobs/worker/manager/PlaywrightManager.java`

上游固定 `.setHeadless(false)`（有头 Chrome 会弹窗占用桌面）。增加环境变量开关：

```java
private static final boolean HEADLESS = isTruthy(System.getenv("OB_BROWSER_HEADLESS"));
...
.setHeadless(HEADLESS)
```

控制面通过 `RUNNER_HEADLESS=1` 向 runner 注入 `OB_BROWSER_HEADLESS=1`，浏览器在后台运行，不出现可见窗口；登录二维码仍可通过 `/api/offerbuddy/qr` 截图拿到，在网页/聊天里展示。

> 注意：Boss 有概率对无头浏览器做风险控制（验证码/限制）；上游默认有头也是这个原因。遇到风控时可临时切回有头模式排查。

## 4. 通用浏览器工具（任意网申）

新增文件：

- `worker/service/CustomBrowserService.java`：在 PlaywrightManager 的持久化上下文里维护一个独立标签页，提供 open / snapshot / click / fill / select / check / press / upload / screenshot
- `application/controller/BrowserController.java`：`/api/browser/*` 接口，由 worker secret 保护；配合 `BrowserNetworkPolicy` 检查 URL 和部分浏览器请求，`TrustedBrowserUpload` 只读取 runner 工作目录下 `chat-uploads` 的直接文件。

配套修改：

- `PlaywrightManager`：暴露 `getContext()` 供通用浏览器使用
- `OfferbuddyWorkerFilter`：`ALLOWED_PREFIXES` 增加 `/api/browser/`
- 快照会给可交互元素打 `data-gj-ref="eN"`，agent 用 ref 操作；页面变化后需要重新 snapshot
- 这些请求检查不能替代网络隔离：DNS 解析与连接之间存在竞争，重定向链、弹窗首个请求、Service Worker 和 WebSocket 不保证全部覆盖。runner 仍是进程级隔离，不得视为多租户安全沙箱。

## 5. Boss 单轮名额与停止

`POST /api/boss/start`（以及 `/execute`）可选 JSON `{"maxCount": 5}`；不传则不限量。成功响应返回生效的 `maxCount`。同一 runner 的 Boss 任务在接收启动请求时即占用运行状态，重复启动不会产生第二轮。

名额按岗位沟通入口点击前的占用次数计算，首次“立即沟通”可能自行发送平台默认招呼，继续沟通也占一个名额；调试模式、过滤和未找到入口不占用。执行点原子检查停止及上限，异常时名额不回退：页面可能已执行点击或发送，不能保证消息送达，也可能未实际点击。`attemptedCount` 是占用数，不是成功投递数，单轮可能因岗位不足或错误而低于上限。图片和文字属于该岗位后续动作，不再额外计数。

`POST /api/boss/stop` 的成功只表示请求已接收；`/api/boss/status` 的 `isRunning` 在清理完成后才变为 false，`stopping` 表示停止进行中，`outcome` 为 `idle`、`running`、`stopping`、`completed`、`stopped` 或 `failed`。停止是协作式的：已进入的浏览器操作、AI 调用或图片发送不能撤销，也可能需要等待其超时；停止请求和动作之间的竞争可能使已占用名额的动作继续。控制面仅监视状态，不靠轮询阻止超额。

猎聘启动同样在提交异步任务前原子占用运行状态，避免并发启动返回成功；启动成功不代表实际投递成功。

## 未修改但依赖的上游行为

- `OB_WORKER_SECRET` 存在时：不自动打开管理页、不监听 6866、启动时不初始化 Playwright（浏览器按需拉起，利于多实例省内存）
- 数据库路径 `./db/getjobs.db`、Chrome profile `./browser-data` 都相对工作目录，因此每个 runner 使用独立工作目录即可隔离
- `/api/offerbuddy/login`、`/api/offerbuddy/qr`、`/api/offerbuddy/shutdown` 提供登录与退出能力（需 secret）