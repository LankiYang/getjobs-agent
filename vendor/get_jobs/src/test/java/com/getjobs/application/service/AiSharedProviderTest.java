package com.getjobs.application.service;

import com.sun.net.httpserver.HttpServer;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AiSharedProviderTest {
    @Test
    void usesSharedAnthropicMessagesAndProxyManagedAuthentication() throws Exception {
        AtomicReference<String> path = new AtomicReference<>();
        AtomicReference<String> authorization = new AtomicReference<>();
        AtomicReference<String> version = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            path.set(exchange.getRequestURI().getPath());
            authorization.set(exchange.getRequestHeaders().getFirst("Authorization"));
            version.set(exchange.getRequestHeaders().getFirst("anthropic-version"));
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] response = "{\"content\":[{\"type\":\"thinking\",\"thinking\":\"ignored\"},{\"type\":\"text\",\"text\":\"您好\"}]}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            AiService service = new AiService(mock(ConfigService.class), null);
            Map<String, String> env = Map.of("AGENT_PROVIDER", "anthropic", "AGENT_ANTHROPIC_BASE_URL", "http://127.0.0.1:" + server.getAddress().getPort(),
                    "AGENT_MODEL", "deepseek-v4.1-flash-1017", "AGENT_API_KEY", "PROXY_MANAGED");
            assertEquals("您好", service.sendRequest("测试提示词", env));
            assertEquals("/v1/messages", path.get());
            assertNull(authorization.get());
            assertEquals("2023-06-01", version.get());
            JSONObject sent = new JSONObject(body.get());
            assertEquals("deepseek-v4.1-flash-1017", sent.getString("model"));
            assertEquals("您好", service.sendRequest("测试", Map.of("AGENT_PROVIDER", "anthropic",
                    "AGENT_ANTHROPIC_BASE_URL", "http://127.0.0.1:" + server.getAddress().getPort(),
                    "AGENT_API_KEY", "test-token")));
            assertEquals("Bearer test-token", authorization.get());
            assertEquals(4096, sent.getInt("max_tokens"));
            assertEquals("测试提示词", sent.getJSONArray("messages").getJSONObject(0).getString("content"));
        } finally {
            server.stop(0);
        }
    }

    @Test
    void incompleteOwnConfigDoesNotSilentlyUseSharedModel() {
        ConfigService config = mock(ConfigService.class);
        when(config.getConfigValue("BASE_URL")).thenReturn("https://example.invalid");
        AiService service = new AiService(config, null);
        assertThrows(IllegalStateException.class, () -> service.sendRequest("test", Map.of("AGENT_PROVIDER", "anthropic")));
    }
}
