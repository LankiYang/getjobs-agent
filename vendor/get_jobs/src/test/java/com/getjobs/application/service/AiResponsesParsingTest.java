package com.getjobs.application.service;

import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class AiResponsesParsingTest {
    @Test
    void parsesResponsesOutputInsteadOfSendingRawJson() {
        JSONObject response = new JSONObject("""
                {"output":[{"type":"reasoning"},{"type":"message","content":[
                  {"type":"output_text","text":"您好，"},
                  {"type":"output_text","text":"期待沟通"}]}]}
                """);
        assertEquals("您好，期待沟通", AiService.extractResponsesText(response));
        assertEquals("false", AiService.extractResponsesText(new JSONObject(
                "{\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"false\"}]}]}")));
        assertNull(AiService.extractResponsesText(new JSONObject("{\"output\":[]}")));
        assertEquals("兼容回复", AiService.extractResponsesText(new JSONObject(
                "{\"choices\":[{\"message\":{\"content\":\"兼容回复\"}}]}")));
    }
}
