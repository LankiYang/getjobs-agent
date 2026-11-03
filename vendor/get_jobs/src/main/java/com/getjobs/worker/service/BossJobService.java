package com.getjobs.worker.service;

import com.getjobs.application.service.ConfigService;
import com.getjobs.worker.boss.Boss;
import com.getjobs.worker.boss.BossConfig;
import com.getjobs.worker.dto.JobProgressMessage;
import com.getjobs.worker.manager.PlaywrightManager;
import com.microsoft.playwright.Page;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

/**
 * Boss直聘任务服务
 * 管理Boss平台的投递任务执行和状态
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class BossJobService implements JobPlatformService {
    private static final String PLATFORM = "boss";

    private final PlaywrightManager playwrightManager;
    private final ConfigService configService;
    private final ObjectProvider<Boss> bossProvider;

    // 任务运行状态
    private volatile boolean isRunning = false;
    // 停止标志
    private volatile boolean shouldStop = false;
    // 本次任务的开始时间（毫秒），用于判断任务是不是卡死了
    private volatile long runningSince = 0L;
    private volatile long completedTasks = 0L;
    private volatile int attemptedCount = 0;
    private volatile Integer maxCount = null;
    private volatile String outcome = "idle";

    private synchronized boolean reserveRun(Integer limit) {
        if (isRunning) return false;
        isRunning = true;
        shouldStop = false;
        runningSince = System.currentTimeMillis();
        attemptedCount = 0;
        maxCount = limit;
        outcome = "running";
        return true;
    }

    public boolean startDelivery(Integer limit, Consumer<JobProgressMessage> callback) {
        if (!reserveRun(limit)) return false;
        try {
            CompletableFuture.runAsync(() -> runDelivery(callback));
            return true;
        } catch (RuntimeException e) {
            synchronized (this) {
                outcome = "failed";
                runningSince = 0;
                isRunning = false;
            }
            throw e;
        }
    }

    private synchronized boolean reserveAttempt() {
        if (shouldStop || (maxCount != null && attemptedCount >= maxCount)) return false;
        attemptedCount++;
        return true;
    }

    private boolean canStartAttempt() {
        return !shouldStop && (maxCount == null || attemptedCount < maxCount);
    }

    @Override
    public void executeDelivery(Consumer<JobProgressMessage> progressCallback) {
        if (!reserveRun(null)) {
            progressCallback.accept(JobProgressMessage.warning(PLATFORM, "任务已在运行中"));
            return;
        }
        runDelivery(progressCallback);
    }

    private void runDelivery(Consumer<JobProgressMessage> progressCallback) {
        boolean completed = false;
        boolean monitoringPaused = false;
        try {
            // 浏览器可能已经被关掉（手动关窗口 / Chrome 崩溃），这时各字段仍非空，
            // 但每个操作都会抛 TargetClosedError。先确保浏览器可用，必要时重新拉起。
            progressCallback.accept(JobProgressMessage.info(PLATFORM, "检查浏览器状态..."));
            playwrightManager.ensureReady();

            // 获取Boss页面实例
            Page page = playwrightManager.getBossPage();
            if (page == null) {
                progressCallback.accept(JobProgressMessage.error(PLATFORM, "Boss页面未初始化"));
                return;
            }

            // 检查是否已登录
            if (!playwrightManager.isLoggedIn(PLATFORM)) {
                progressCallback.accept(JobProgressMessage.error(PLATFORM, "请先登录Boss直聘"));
                return;
            }

            if (shouldStop) return;

            // 暂停后台登录监控，避免与投递流程并发访问同一Page
            playwrightManager.pauseBossMonitoring();
            monitoringPaused = true;

            // 加载配置（统一从 boss_config 专表读取）
            BossConfig config = configService.getBossConfig();
            progressCallback.accept(JobProgressMessage.info(PLATFORM, "配置加载成功"));

            progressCallback.accept(JobProgressMessage.info(PLATFORM, "开始投递任务..."));

            // 创建Boss实例并执行投递
            Boss.ProgressCallback bossCallback = (message, current, total) -> {
                if (current != null && total != null) {
                    progressCallback.accept(JobProgressMessage.progress(PLATFORM, message, current, total));
                } else {
                    progressCallback.accept(JobProgressMessage.info(PLATFORM, message));
                }
            };

            Boss boss = bossProvider.getObject();
            boss.setPage(page);
            boss.setConfig(config);
            boss.setProgressCallback(bossCallback);
            boss.setShouldStopCallback(this::shouldStop);
            boss.setCanStartAttemptCallback(this::canStartAttempt);
            boss.setReserveAttemptCallback(this::reserveAttempt);
            boss.prepare();

            // 整个投递流程必须跑在 Playwright 专用线程上。
            // 原来跑在 ForkJoinPool 上，会和 scheduling-1 的登录监控同时操作同一条
            // Playwright 连接，导航被打断报 net::ERR_ABORTED，整个任务直接挂掉。
            int deliveredCount = playwrightManager.callOnPlaywright(boss::execute);

            completed = true;
            progressCallback.accept(JobProgressMessage.info(PLATFORM,
                String.format("任务%s，已尝试%d个岗位沟通，文字发送完成%d次", shouldStop ? "已停止" : "结束", attemptedCount, deliveredCount)));
        } catch (Exception e) {
            log.error("Boss投递任务执行失败", e);
            progressCallback.accept(JobProgressMessage.error(PLATFORM, "投递失败: " + e.getMessage()));
        } finally {
            // Keep running true until cleanup has finished.
            // 恢复后台登录监控
            try {
                if (monitoringPaused) playwrightManager.resumeBossMonitoring();
            } catch (Exception ignored) {}
            synchronized (this) {
                outcome = shouldStop ? "stopped" : (completed ? "completed" : "failed");
                shouldStop = false;
                runningSince = 0L;
                completedTasks++;
                isRunning = false;
            }
        }
    }

    @Override
    public synchronized void stopDelivery() {
        if (!isRunning) return;
        log.info("收到停止Boss投递任务的请求");
        shouldStop = true;
        outcome = "stopping";
    }

    /** 任务已经跑了多久（毫秒）；没在跑返回 0 */
    public long runningForMillis() {
        long since = runningSince;
        return since == 0L ? 0L : System.currentTimeMillis() - since;
    }

    @Override
    public Map<String, Object> getStatus() {
        Map<String, Object> status = new HashMap<>();
        status.put("platform", PLATFORM);
        status.put("isRunning", isRunning);
        status.put("stopping", shouldStop);
        status.put("attemptedCount", attemptedCount);
        status.put("maxCount", maxCount);
        status.put("outcome", outcome);
        status.put("isLoggedIn", playwrightManager.isLoggedIn(PLATFORM));
        status.put("completedTasks", completedTasks);
        status.put("loginState", playwrightManager.getOfferbuddyLoginState());
        status.put("browserReady", playwrightManager.isInitialized());
        return status;
    }

    @Override
    public String getPlatformName() {
        return PLATFORM;
    }

    @Override
    public boolean isRunning() {
        return isRunning;
    }

    /**
     * 检查是否应该停止
     * 供Boss.java调用
     */
    public boolean shouldStop() {
        return shouldStop;
    }

    
}
