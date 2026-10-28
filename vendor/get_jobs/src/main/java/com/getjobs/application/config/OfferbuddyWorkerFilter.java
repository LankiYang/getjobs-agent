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
    private static final Set<String> ALLOWED = Set.of(
            "/api/offerbuddy/login", "/api/offerbuddy/resume", "/api/offerbuddy/qr", "/api/offerbuddy/shutdown", "/api/boss/status",
            "/api/boss/config", "/api/boss/config/resume-image", "/api/boss/start", "/api/boss/stop", "/api/boss/list");
    private final String secret = System.getenv("OB_WORKER_SECRET");

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
                !ALLOWED.contains(request.getRequestURI()) || request.getHeader("Origin") != null) {
            response.sendError(HttpServletResponse.SC_FORBIDDEN);
            return;
        }
        chain.doFilter(request, response);
    }
}
