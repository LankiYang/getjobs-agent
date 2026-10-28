package com.getjobs.application.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Set;

/** Restricts the optional Offerbuddy worker mode to its authenticated API. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class OfferbuddyWorkerFilter extends OncePerRequestFilter {
    private static final java.util.List<String> ALLOWED_PREFIXES = java.util.List.of(
            "/api/offerbuddy/", "/api/boss/", "/api/liepin/", "/api/51job/", "/api/jobs/",
            "/api/zhilian/", "/api/ai/", "/api/config", "/api/cookie", "/api/health", "/api/playwright/");
    private final String secret = System.getenv("OB_WORKER_SECRET");

    private static boolean isAllowed(String uri) {
        for (String prefix : ALLOWED_PREFIXES) {
            if (uri.startsWith(prefix)) return true;
        }
        return false;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (secret == null) {
            if (request.getRequestURI().startsWith("/api/offerbuddy/")) response.sendError(HttpServletResponse.SC_FORBIDDEN);
            else chain.doFilter(request, response);
            return;
        }
        String supplied = request.getHeader("X-Offerbuddy-Worker-Secret");
        if (secret.length() < 32 || supplied == null ||
                !MessageDigest.isEqual(secret.getBytes(StandardCharsets.UTF_8), supplied.getBytes(StandardCharsets.UTF_8)) ||
                !isAllowed(request.getRequestURI()) || request.getHeader("Origin") != null) {
            response.sendError(HttpServletResponse.SC_FORBIDDEN);
            return;
        }
        chain.doFilter(request, response);
    }
}
