package com.getjobs.worker.service;

import com.getjobs.application.service.ConfigService;
import com.getjobs.worker.liepin.Liepin;
import com.getjobs.worker.manager.PlaywrightManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class LiepinJobStartTest {
    @Test
    void reservesBeforeAsyncStartupAndRejectsConcurrentStart() throws Exception {
        PlaywrightManager manager = mock(PlaywrightManager.class);
        ConfigService config = mock(ConfigService.class);
        @SuppressWarnings("unchecked") ObjectProvider<Liepin> provider = mock(ObjectProvider.class);
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        CountDownLatch finished = new CountDownLatch(1);
        doAnswer(invocation -> { entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); return null; })
                .when(manager).ensureReady();
        doAnswer(invocation -> { finished.countDown(); return null; }).when(manager).resumeLiepinMonitoring();
        LiepinJobService service = new LiepinJobService(manager, config, provider);
        try {
            assertTrue(service.startDelivery(message -> {}));
            assertTrue(entered.await(5, TimeUnit.SECONDS));
            assertTrue(service.isRunning());
            assertFalse(service.startDelivery(message -> {}));
            service.stopDelivery();
            assertTrue(service.shouldStop());
        } finally {
            release.countDown();
        }
        assertTrue(finished.await(5, TimeUnit.SECONDS));
    }
}
