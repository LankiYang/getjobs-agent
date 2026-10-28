package com.getjobs.application.controller;

import com.getjobs.worker.manager.PlaywrightManager;
import com.getjobs.worker.service.BossJobService;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import java.util.Map;

@RestController
@RequestMapping("/api/offerbuddy")
public class OfferbuddyWorkerController {
    private final PlaywrightManager browser;
    private final BossJobService jobs;
    public OfferbuddyWorkerController(PlaywrightManager browser, BossJobService jobs) {
        this.browser = browser;
        this.jobs = jobs;
    }

    @PostMapping("/login")
    public ResponseEntity<?> login() {
        try {
            browser.openOfferbuddyLogin();
            boolean loggedIn = browser.isLoggedIn("boss");
            return ResponseEntity.ok(Map.of("ok", true, "loggedIn", loggedIn,
                    "message", loggedIn ? "已恢复登录会话" : "已打开登录页，请获取二维码"));
        } catch (IllegalStateException e) {
            return ResponseEntity.status(409).body(Map.of("message", e.getMessage()));
        }
    }

    @PostMapping("/resume")
    public Map<String, Object> resume() {
        if (jobs.isRunning()) throw new IllegalStateException("任务运行中，不能重新打开岗位页面");
        return browser.resumeOfferbuddySession();
    }

    @PostMapping("/shutdown")
    public ResponseEntity<Map<String, Object>> shutdown() {
        if (jobs.isRunning()) return ResponseEntity.status(409).body(Map.of("ok", false));
        browser.runOnPlaywright(browser::destroy);
        Thread shutdown = new Thread(() -> {
            try { Thread.sleep(500); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            System.exit(0);
        }, "offerbuddy-worker-shutdown");
        shutdown.setDaemon(false);
        shutdown.start();
        return ResponseEntity.ok(Map.of("ok", true));
    }

    @GetMapping(value = "/qr", produces = MediaType.IMAGE_PNG_VALUE)
    public ResponseEntity<?> qr() {
        try {
            return ResponseEntity.ok().cacheControl(CacheControl.noStore()).contentType(MediaType.IMAGE_PNG)
                    .body(browser.offerbuddyLoginQr());
        } catch (IllegalStateException e) {
            return ResponseEntity.status(409).cacheControl(CacheControl.noStore()).contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("message", e.getMessage()));
        }
    }
}
