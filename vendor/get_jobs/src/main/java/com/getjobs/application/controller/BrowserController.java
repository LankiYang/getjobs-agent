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

    @PostMapping("/click-text")
    public Map<String, Object> clickText(@RequestBody Map<String, String> body) {
        browser.clickText(body.getOrDefault("text", ""));
        return Map.of("ok", true);
    }

    @PostMapping("/click-selector")
    public Map<String, Object> clickSelector(@RequestBody Map<String, Object> body) {
        String selector = String.valueOf(body.getOrDefault("selector", "")).trim();
        if (selector.isEmpty() || selector.length() > 300) {
            throw new IllegalArgumentException("selector 不能为空且不能超过 300 字符");
        }
        int index = intValue(body.get("index"), 0);
        boolean force = Boolean.parseBoolean(String.valueOf(body.getOrDefault("force", false)));
        browser.clickSelector(selector, index, force);
        return Map.of("ok", true);
    }

    @PostMapping("/click-xy")
    public Map<String, Object> clickXY(@RequestBody Map<String, Object> body) {
        double x = doubleValue(body.get("x"));
        double y = doubleValue(body.get("y"));
        if (x < 0 || y < 0 || x > 10000 || y > 10000) {
            throw new IllegalArgumentException("坐标超出范围");
        }
        browser.clickXY(x, y);
        return Map.of("ok", true);
    }

    @PostMapping("/type")
    public Map<String, Object> type(@RequestBody Map<String, String> body) {
        String text = body.getOrDefault("text", "");
        if (text.isEmpty() || text.length() > 5000) {
            throw new IllegalArgumentException("text 长度需在 1-5000 之间");
        }
        browser.typeText(text);
        return Map.of("ok", true);
    }

    @PostMapping("/scroll")
    public Map<String, Object> scroll(@RequestBody Map<String, Object> body) {
        double delta = doubleValue(body.get("deltaY"));
        if (Math.abs(delta) > 20000) {
            throw new IllegalArgumentException("滚动距离过大");
        }
        browser.scroll(delta);
        return Map.of("ok", true);
    }

    @PostMapping("/wait")
    public Map<String, Object> wait(@RequestBody Map<String, Object> body) {
        int millis = intValue(body.get("ms"), 1000);
        if (millis < 0 || millis > 15000) {
            throw new IllegalArgumentException("等待时间需在 0-15000 毫秒之间");
        }
        browser.waitFor(millis);
        return Map.of("ok", true);
    }

    @GetMapping("/links")
    public Map<String, Object> links() {
        return Map.of("ok", true, "links", browser.links());
    }

    @GetMapping("/pages")
    public Map<String, Object> pages() {
        return Map.of("ok", true, "pages", browser.pages());
    }

    @PostMapping("/switch-page")
    public Map<String, Object> switchPage(@RequestBody Map<String, Object> body) {
        Object value = body.getOrDefault("index", 0);
        int index = value instanceof Number number ? number.intValue() : Integer.parseInt(String.valueOf(value));
        return browser.switchPage(index);
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

    @PostMapping("/upload-file")
    public ResponseEntity<?> uploadFile(@RequestBody Map<String, String> body) {
        String path = body.getOrDefault("path", "").trim();
        if (path.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("message", "path 不能为空"));
        }
        java.nio.file.Path file;
        try {
            file = java.nio.file.Paths.get(path).toAbsolutePath().normalize();
        } catch (Exception e) {
            return ResponseEntity.badRequest().body(Map.of("message", "文件路径不正确"));
        }
        String lower = file.getFileName().toString().toLowerCase(java.util.Locale.ROOT);
        if (!lower.matches(".*\\.(pdf|doc|docx|png|jpg|jpeg)$")) {
            return ResponseEntity.badRequest().body(Map.of("message", "只支持 pdf/doc/docx/png/jpg 文件"));
        }
        try {
            if (!java.nio.file.Files.isRegularFile(file)) {
                return ResponseEntity.badRequest().body(Map.of("message", "文件不存在"));
            }
            if (java.nio.file.Files.size(file) > 20L * 1024 * 1024) {
                return ResponseEntity.badRequest().body(Map.of("message", "文件不能超过 20MB"));
            }
        } catch (Exception e) {
            return ResponseEntity.badRequest().body(Map.of("message", "无法读取文件"));
        }
        browser.uploadFile(body.get("ref"), body.get("selector"), file.toString());
        return ResponseEntity.ok(Map.of("ok", true, "file", file.getFileName().toString()));
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

    private static int intValue(Object value, int fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (Exception e) {
            return fallback;
        }
    }

    private static double doubleValue(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            return Double.parseDouble(String.valueOf(value));
        } catch (Exception e) {
            throw new IllegalArgumentException("坐标或距离必须是数字");
        }
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