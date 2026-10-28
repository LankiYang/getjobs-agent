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

## 未修改但依赖的上游行为

- `OB_WORKER_SECRET` 存在时：不自动打开管理页、不监听 6866、启动时不初始化 Playwright（浏览器按需拉起，利于多实例省内存）
- 数据库路径 `./db/getjobs.db`、Chrome profile `./browser-data` 都相对工作目录，因此每个 runner 使用独立工作目录即可隔离
- `/api/offerbuddy/login`、`/api/offerbuddy/qr`、`/api/offerbuddy/shutdown` 提供登录与退出能力（需 secret）