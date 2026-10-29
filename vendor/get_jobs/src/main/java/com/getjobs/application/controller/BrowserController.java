package com.getjobs.application.controller;

import com.getjobs.application.service.BossResumeImageService;
import com.getjobs.worker.service.CustomBrowserService;
import com.microsoft.playwright.PlaywrightException;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.net.URI;
import java.util.Map;
import java.util.Optional;

/**
 * 通用浏览器操作接口：供 agent 在任意网申页面读表单、填字段、上传简历。
 * 由 OfferbuddyWorkerFilter 的 worker secret 保护，只监听内网。
 */
@RestController
@RequestMapping("/api/browser")
@RequiredArgsConstructor
public class BrowserController {

    private final CustomBrowserService browser;
    private final BossResumeImageService resumeImageService;

    @PostMapping("/open")
    public ResponseEntity<?> open(@RequestBody Map<String, String> body) {
        String url = body.getOrDefault("url", "").trim();
        String error = validateUrl(url);
        if (error != null) {
            return ResponseEntity.badRequest().body(Map.of("message", error));
        }
        return ResponseEntity.ok(browser.open(url));
    }

    @GetMapping("/state")
    public Map<String, Object> state() {
        return browser.state();
    }

    @GetMapping("/snapshot")
    public Map<String, Object> snapshot() {
        return Map.of("ok", true, "snapshot", browser.snapshot());
    }

    @PostMapping("/click")
    public Map<String, Object> click(@RequestBody Map<String, String> body) {
        browser.click(body.get("ref"));
        return Map.of("ok", true);
    }

    @PostMapping("/fill")
    public Map<String, Object> fill(@RequestBody Map<String, String> body) {
        browser.fill(body.get("ref"), body.get("value"));
        return Map.of("ok", true);
    }

    @PostMapping("/select")
    public Map<String, Object> select(@RequestBody Map<String, String> body) {
        browser.select(body.get("ref"), body.get("value"));
        return Map.of("ok", true);
    }

    @PostMapping("/check")
    public Map<String, Object> check(@RequestBody Map<String, String> body) {
        browser.check(body.get("ref"));
        return Map.of("ok", true);
    }

    @PostMapping("/press")
    public Map<String, Object> press(@RequestBody Map<String, String> body) {
        browser.press(body.get("key"));
        return Map.of("ok", true);
    }

    @PostMapping("/upload-resume")
    public ResponseEntity<?> uploadResume(@RequestBody Map<String, String> body) {
        Optional<BossResumeImageService.ResumeImageInfo> info = resumeImageService.getCurrentImage();
        if (info.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("message", "还没有上传图片简历"));
        }
        browser.upload(body.get("ref"), info.get().path().toString());
        return ResponseEntity.ok(Map.of("ok", true, "file", info.get().fileName()));
    }

    @GetMapping(value = "/screenshot", produces = MediaType.IMAGE_PNG_VALUE)
    public byte[] screenshot() {
        return browser.screenshot();
    }

    @ExceptionHandler({IllegalArgumentException.class, PlaywrightException.class})
    public ResponseEntity<Map<String, Object>> handleActionError(Exception e) {
        String message = e.getMessage() == null ? "浏览器操作失败" : e.getMessage();
        if (message.length() > 300) {
            message = message.substring(0, 300);
        }
        return ResponseEntity.badRequest().body(Map.of("message", "浏览器操作失败: " + message));
    }

    private static String validateUrl(String url) {
        if (url.isEmpty()) {
            return "url 不能为空";
        }
        try {
            URI uri = URI.create(url);
            String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
            if (!scheme.equals("http") && !scheme.equals("https")) {
                return "只支持 http/https 地址";
            }
            String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase();
            if (host.isEmpty()) {
                return "URL 缺少主机名";
            }
            if (host.equals("localhost") || host.endsWith(".local") || host.equals("::1")
                    || host.startsWith("127.") || host.startsWith("10.")
                    || host.startsWith("192.168.") || host.startsWith("169.254.")) {
                return "不允许访问本机或内网地址";
            }
            if (host.startsWith("172.")) {
                String[] parts = host.split("\\.");
                if (parts.length > 1) {
                    try {
                        int second = Integer.parseInt(parts[1]);
                        if (second >= 16 && second <= 31) {
                            return "不允许访问内网地址";
                        }
                    } catch (NumberFormatException ignored) {
                        // 非数字主机名，按外网处理
                    }
                }
            }
            return null;
        } catch (Exception e) {
            return "URL 格式不正确";
        }
    }
}