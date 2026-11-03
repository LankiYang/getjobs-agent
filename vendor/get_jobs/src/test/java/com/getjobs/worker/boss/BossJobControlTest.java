package com.getjobs.worker.boss;

import com.getjobs.application.service.ConfigService;
import com.getjobs.worker.manager.PlaywrightManager;
import com.getjobs.worker.service.BossJobService;
import com.microsoft.playwright.Page;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class BossJobControlTest {
    private static void awaitStopped(BossJobService service) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (service.isRunning() && System.nanoTime() < deadline) Thread.sleep(10);
        assertFalse(service.isRunning(), "任务未结束");
    }

    @Test void limitRejectsFurtherAttemptsAcrossOneRun() throws Exception {
        PlaywrightManager manager = mock(PlaywrightManager.class);
        ConfigService config = mock(ConfigService.class);
        @SuppressWarnings("unchecked") ObjectProvider<Boss> provider = mock(ObjectProvider.class);
        Boss boss = mock(Boss.class);
        when(manager.getBossPage()).thenReturn(mock(Page.class));
        when(manager.isLoggedIn("boss")).thenReturn(true);
        when(provider.getObject()).thenReturn(boss);
        when(manager.callOnPlaywright(any())).thenAnswer(inv -> ((Callable<?>) inv.getArgument(0)).call());
        AtomicReference<Supplier<Boolean>> reserve = new AtomicReference<>();
        doAnswer(inv -> { reserve.set(inv.getArgument(0)); return null; }).when(boss).setReserveAttemptCallback(any());
        AtomicInteger accepted = new AtomicInteger();
        when(boss.execute()).thenAnswer(inv -> {
            for (int i = 0; i < 10; i++) if (reserve.get().get()) accepted.incrementAndGet();
            return 0;
        });
        BossJobService service = new BossJobService(manager, config, provider);
        assertTrue(service.startDelivery(3, msg -> {}));
        awaitStopped(service);
        assertEquals(3, accepted.get());
        assertEquals(3, service.getStatus().get("attemptedCount"));
        assertEquals(3, service.getStatus().get("maxCount"));
        assertEquals("completed", service.getStatus().get("outcome"));
    }

    @Test void stoppingDuringStartupPreventsWorkerCreation() throws Exception {
        PlaywrightManager manager = mock(PlaywrightManager.class);
        ConfigService config = mock(ConfigService.class);
        @SuppressWarnings("unchecked") ObjectProvider<Boss> provider = mock(ObjectProvider.class);
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        doAnswer(inv -> { entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)); return null; }).when(manager).ensureReady();
        when(manager.getBossPage()).thenReturn(mock(Page.class));
        when(manager.isLoggedIn("boss")).thenReturn(true);
        BossJobService service = new BossJobService(manager, config, provider);
        try {
            assertTrue(service.startDelivery(3, msg -> {}));
            assertTrue(entered.await(5, TimeUnit.SECONDS));
            assertFalse(service.startDelivery(3, msg -> {}), "并发启动应被拒绝");
            service.stopDelivery();
            assertTrue(service.isRunning());
            assertEquals(true, service.getStatus().get("stopping"));
        } finally {
            release.countDown();
        }
        awaitStopped(service);
        assertEquals("stopped", service.getStatus().get("outcome"));
        assertEquals(0, service.getStatus().get("attemptedCount"));
        verify(provider, never()).getObject();
    }
}
