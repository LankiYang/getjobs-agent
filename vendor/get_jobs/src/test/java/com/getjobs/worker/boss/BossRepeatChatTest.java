package com.getjobs.worker.boss;

import com.getjobs.application.service.BossService;
import com.getjobs.application.entity.AiEntity;
import com.getjobs.application.service.AiService;
import com.getjobs.application.service.BossResumeImageService;
import com.getjobs.worker.utils.Job;
import com.getjobs.worker.utils.PlaywrightUtil;
import com.microsoft.playwright.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class BossRepeatChatTest {
    @ParameterizedTest
    @CsvSource({"立即沟通,false,true,false", "立即沟通,true,true,false", "继续沟通,false,false,false", "继续沟通,true,true,false", "立即沟通,,true,false", "继续沟通,,false,false", "已关闭,true,false,false", "继续沟通,true,false,true"})
    void sendsOnlyWhenButtonAndSwitchAllow(String text, Boolean enabled, boolean expected, boolean emptyMessages) throws Exception {
        BossService service = mock(BossService.class);
        Boss boss = new Boss(service, null, null);
        BossConfig config = new BossConfig();
        config.setAllowRepeatChat(enabled);
        config.setSayHi("您好，希望进一步沟通。");
        config.setRepeatChatMessages(emptyMessages ? " \n\t" : "\n  自定义跟进消息  \n\n");
        config.setEnableAI(true);
        boss.setConfig(config);
        Page listing = mock(Page.class);
        Page detail = mock(Page.class);
        BrowserContext context = mock(BrowserContext.class);
        Locator more = mock(Locator.class);
        Locator button = mock(Locator.class);
        Locator input = mock(Locator.class);
        Locator send = mock(Locator.class);
        when(listing.locator(anyString())).thenReturn(more);
        when(more.count()).thenReturn(1);
        when(more.first()).thenReturn(more);
        when(more.getAttribute("href")).thenReturn("/job_detail/test.html");
        when(listing.context()).thenReturn(context);
        when(context.newPage()).thenReturn(detail);
        when(detail.locator("a.btn-startchat, a.op-btn-chat")).thenReturn(button);
        when(button.count()).thenReturn(1);
        when(button.first()).thenReturn(button);
        when(button.textContent()).thenReturn(text);
        when(detail.locator("div#chat-input.chat-input[contenteditable='true'], textarea.input-area")).thenReturn(input);
        when(input.count()).thenReturn(1);
        when(input.first()).thenReturn(input);
        when(input.isVisible()).thenReturn(true);
        when(input.evaluate(anyString())).thenReturn("textarea");
        when(detail.locator("div.send-message, button[type='send'].btn-send, button.btn-send")).thenReturn(send);
        when(send.count()).thenReturn(1);
        when(send.first()).thenReturn(send);
        boss.setPage(listing);
        var submission = Boss.class.getDeclaredMethod("resumeSubmission", String.class, Job.class);
        submission.setAccessible(true);
        Job job = new Job();
        if ("继续沟通".equals(text)) job.setJobInfo("已有会话不会调用AI重新分析此JD");
        try (var ignored = mockStatic(PlaywrightUtil.class);
             var navigators = mockConstruction(BossChatNavigator.class, (navigator, construction) -> {
                 when(navigator.open(any(), anyBoolean(), anyBoolean(), any(), any(), anyLong())).thenAnswer(invocation -> {
                     button.click();
                     return new BossChatNavigator.Chat(detail, input, "继续沟通".equals(text));
                 });
             })) {
            assertEquals(expected, submission.invoke(boss, "Java", job));
        }
        verify(button, times(expected ? 1 : 0)).click();
        verify(input, times(expected ? 1 : 0)).fill("继续沟通".equals(text) ? "自定义跟进消息" : config.getSayHi());
        verify(send, times(expected ? 1 : 0)).click();
        verify(detail).close();
    }

    @ParameterizedTest
    @CsvSource({"false,reply,默认,false", "true,reply,生成招呼,true", "true,empty,默认,true", "true,error,默认,true", "true,invalid,默认,false", "true,reject,NONE,true", "true,nojd,默认,false"})
    void firstChatHonoursAiSwitchAndFallback(boolean enabled, String mode, String expected, boolean callsAi) throws Exception {
        AiService ai = mock(AiService.class);
        Boss boss = new Boss(mock(BossService.class), ai, null);
        BossConfig config = new BossConfig();
        config.setEnableAI(enabled);
        config.setSayHi("默认");
        boss.setConfig(config);
        AiEntity aiConfig = new AiEntity();
        aiConfig.setIntroduce("技能");
        aiConfig.setPrompt("invalid".equals(mode) ? "%q" : "%s|%s|%s|%s|%s");
        when(ai.getAiConfig()).thenReturn(aiConfig);
        when(ai.sendRequest(anyString())).thenAnswer(invocation -> {
            if ("error".equals(mode)) throw new IllegalStateException("offline");
            if ("empty".equals(mode)) return "  ";
            if ("reject".equals(mode)) return "false";
            return "生成招呼";
        });
        Page listing = mock(Page.class), detail = mock(Page.class);
        BrowserContext context = mock(BrowserContext.class);
        Locator more = mock(Locator.class), button = mock(Locator.class), input = mock(Locator.class), send = mock(Locator.class);
        when(listing.locator(anyString())).thenReturn(more);
        when(more.count()).thenReturn(1);
        when(more.first()).thenReturn(more);
        when(more.getAttribute("href")).thenReturn("/job_detail/test.html");
        when(listing.context()).thenReturn(context);
        when(context.newPage()).thenReturn(detail);
        when(detail.locator("a.btn-startchat, a.op-btn-chat")).thenReturn(button);
        when(button.count()).thenReturn(1);
        when(button.first()).thenReturn(button);
        when(button.textContent()).thenReturn("立即沟通");
        when(input.first()).thenReturn(input);
        when(input.evaluate(anyString())).thenReturn("textarea");
        when(detail.locator("div.send-message, button[type='send'].btn-send, button.btn-send")).thenReturn(send);
        when(send.count()).thenReturn(1);
        when(send.first()).thenReturn(send);
        boss.setPage(listing);
        Job job = new Job();
        job.setJobName("岗位");
        job.setJobInfo("nojd".equals(mode) ? "" : "岗位描述");
        var submission = Boss.class.getDeclaredMethod("resumeSubmission", String.class, Job.class);
        submission.setAccessible(true);
        try (var ignored = mockStatic(PlaywrightUtil.class);
             var navigators = mockConstruction(BossChatNavigator.class, (navigator, construction) ->
                     when(navigator.open(any(), anyBoolean(), anyBoolean(), any(), any(), anyLong()))
                             .thenReturn(new BossChatNavigator.Chat(detail, input, false)))) {
            assertEquals(!"NONE".equals(expected), submission.invoke(boss, "搜索", job));
        }
        verify(ai, times(callsAi ? 1 : 0)).sendRequest(anyString());
        if (callsAi) verify(ai).sendRequest("技能|搜索|岗位|岗位描述|默认");
        if ("NONE".equals(expected)) verify(input, never()).fill(anyString());
        else verify(input).fill(expected);
    }

    @ParameterizedTest
    @CsvSource({"false", "true"})
    void imageOptionControlsResumeLookupAfterTextSend(boolean sendImage) throws Exception {
        BossResumeImageService resume = mock(BossResumeImageService.class);
        when(resume.getCurrentImagePath()).thenReturn(java.util.Optional.empty());
        Boss boss = new Boss(mock(BossService.class), mock(AiService.class), resume);
        BossConfig config = new BossConfig();
        config.setSayHi("默认");
        config.setSendImgResume(sendImage);
        boss.setConfig(config);
        Page listing = mock(Page.class), detail = mock(Page.class);
        BrowserContext context = mock(BrowserContext.class);
        Locator more = mock(Locator.class), button = mock(Locator.class), input = mock(Locator.class), send = mock(Locator.class);
        when(listing.locator(anyString())).thenReturn(more);
        when(more.count()).thenReturn(1);
        when(more.first()).thenReturn(more);
        when(more.getAttribute("href")).thenReturn("/job_detail/test.html");
        when(listing.context()).thenReturn(context);
        when(context.newPage()).thenReturn(detail);
        when(detail.locator("a.btn-startchat, a.op-btn-chat")).thenReturn(button);
        when(button.count()).thenReturn(1);
        when(button.first()).thenReturn(button);
        when(button.textContent()).thenReturn("立即沟通");
        when(input.first()).thenReturn(input);
        when(input.evaluate(anyString())).thenReturn("textarea");
        when(detail.locator("div.send-message, button[type='send'].btn-send, button.btn-send")).thenReturn(send);
        when(send.count()).thenReturn(1);
        when(send.first()).thenReturn(send);
        boss.setPage(listing);
        var submission = Boss.class.getDeclaredMethod("resumeSubmission", String.class, Job.class);
        submission.setAccessible(true);
        try (var ignored = mockStatic(PlaywrightUtil.class);
             var navigators = mockConstruction(BossChatNavigator.class, (navigator, construction) ->
                     when(navigator.open(any(), anyBoolean(), anyBoolean(), any(), any(), anyLong()))
                             .thenReturn(new BossChatNavigator.Chat(detail, input, false)))) {
            assertEquals(true, submission.invoke(boss, "搜索", new Job()));
        }
        verify(send).click();
        verify(resume, times(sendImage ? 1 : 0)).getCurrentImagePath();
    }

    @Test
    void selectsOnlyNonBlankLinesAndKeepsUserText() {
        BossConfig config = new BossConfig();
        config.setRepeatChatMessages("\r\n 第一条 \r\n\t\r\n第二条？\r\n");
        for (int i = 0; i < 100; i++) {
            assertTrue(java.util.Set.of("第一条", "第二条？").contains(config.randomRepeatChatMessage()));
        }
        config.setRepeatChatMessages(" \n\t\r\n");
        assertNull(config.randomRepeatChatMessage());
        config.setRepeatChatMessages(null);
        assertNull(config.randomRepeatChatMessage());
    }

    @Test
    void includesSixDefaultFollowUpMessages() {
        BossConfig config = new BossConfig();
        assertEquals(6, config.getRepeatChatMessages().lines().count());
        assertTrue(config.getRepeatChatMessages().contains("请问贵公司还招人吗？"));
    }
}
