package com.getjobs.application.service;

import com.getjobs.application.entity.AiEntity;
import com.getjobs.application.mapper.AiMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.json.JSONArray;
import org.json.JSONObject;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;

/**
 * AI 服务（Spring 管理）
 * 从数据库配置获取 BASE_URL、API_KEY、MODEL 并发起 AI 请求。
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class AiService {
    private final ConfigService configService;
    private final AiMapper aiMapper;

    /**
     * 发送 AI 请求（非流式）并返回回复内容。
     * @param content 用户消息内容
     * @return AI 回复文本
     */
    public String sendRequest(String content) {
        return sendRequest(content, System.getenv());
    }

    String sendRequest(String content, java.util.Map<String, String> env) {
        String baseUrl = configService.getConfigValue("BASE_URL");
        String apiKey = configService.getConfigValue("API_KEY");
        String model = configService.getConfigValue("MODEL");
        boolean hasOwnConfig = !isBlank(baseUrl) || !isBlank(apiKey) || !isBlank(model);
        if (!hasOwnConfig) {
            String provider = env.getOrDefault("AGENT_PROVIDER", "openai").trim();
            if ("anthropic".equalsIgnoreCase(provider)) {
                String sharedBase = firstNonBlank(env.get("AGENT_ANTHROPIC_BASE_URL"), env.get("ANTHROPIC_BASE_URL"), "http://127.0.0.1:15721");
                String sharedModel = firstNonBlank(env.get("AGENT_MODEL"), "deepseek-v4.1-flash-1017");
                String token = firstNonBlank(env.get("AGENT_API_KEY"), env.get("ANTHROPIC_AUTH_TOKEN"), "");
                return sendAnthropicRequest(content, sharedBase, token, sharedModel,
                        firstNonBlank(env.get("AGENT_MAX_TOKENS"), "4096"));
            }
            if (!"openai".equalsIgnoreCase(provider)) {
                throw new IllegalStateException("不支持的对话模型提供方: " + provider);
            }
            baseUrl = env.get("AGENT_BASE_URL");
            apiKey = firstNonBlank(env.get("AGENT_API_KEY"), "");
            model = env.get("AGENT_MODEL");
            if (isBlank(baseUrl) || isBlank(model)) {
                throw new IllegalStateException("未配置 AI 模型：请填写独立配置，或配置控制面对话模型");
            }
        } else if (isBlank(baseUrl) || isBlank(apiKey) || isBlank(model)) {
            throw new IllegalStateException("独立 AI 配置不完整：需要 BASE_URL、API_KEY 和 MODEL");
        }
        // 根据模型类型选择兼容的端点（部分“推理/Reasoning”模型需要使用 Responses API）
        String endpoint = !hasOwnConfig ? normalizeBaseUrl(baseUrl) + "/chat/completions"
                : isResponsesModel(model) ? buildResponsesEndpoint(baseUrl) : buildChatCompletionsEndpoint(baseUrl);

        int timeoutInSeconds = 60;

        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(timeoutInSeconds))
                .build();

        // 构建 JSON 请求体
        JSONObject requestData = new JSONObject();
        requestData.put("model", model);
        requestData.put("temperature", 0.5);
        if (endpoint.endsWith("/responses")) {
            // Responses API 采用 input 字段
            requestData.put("input", content);
            // 如需显式控制推理强度，可按需开启：
            // JSONObject reasoning = new JSONObject();
            // reasoning.put("effort", "medium");
            // requestData.put("reasoning", reasoning);
        } else {
            // Chat Completions API 使用 messages
            JSONArray messages = new JSONArray();
            JSONObject message = new JSONObject();
            message.put("role", "user");
            message.put("content", content);
            messages.put(message);
            requestData.put("messages", messages);
        }

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(endpoint))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .header("Authorization", "Bearer " + apiKey)
                // 某些服务（例如 Azure OpenAI）需要 api-key 头，额外加一层兼容
                .header("api-key", apiKey)
                .POST(HttpRequest.BodyPublishers.ofString(requestData.toString()))
                .build();

        try {
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 200) {
                JSONObject responseObject = new JSONObject(response.body());

                String requestId = responseObject.optString("id");
                long created = responseObject.optLong("created", 0);
                String usedModel = responseObject.optString("model");

                String responseContent;
                if (endpoint.endsWith("/responses")) {
                    responseContent = extractResponsesText(responseObject);
                } else {
                    // Chat Completions API
                    JSONObject messageObject = responseObject.getJSONArray("choices")
                            .getJSONObject(0)
                            .getJSONObject("message");
                    responseContent = messageObject.getString("content");
                }

                JSONObject usageObject = responseObject.optJSONObject("usage");
                int promptTokens = usageObject != null ? usageObject.optInt("prompt_tokens", -1) : -1;
                int completionTokens = usageObject != null ? usageObject.optInt("completion_tokens", -1) : -1;
                int totalTokens = usageObject != null ? usageObject.optInt("total_tokens", -1) : -1;

                LocalDateTime createdTime = created > 0
                        ? Instant.ofEpochSecond(created).atZone(ZoneId.systemDefault()).toLocalDateTime()
                        : LocalDateTime.now();
                DateTimeFormatter formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

                log.info("AI响应: id={}, time={}, model={}, promptTokens={}, completionTokens={}, totalTokens={}",
                        requestId, createdTime.format(formatter), usedModel, promptTokens, completionTokens, totalTokens);

                return responseContent;
            } else {
                // 更详细的错误日志，便于定位 400 问题
                log.error("AI请求失败: status={}, endpoint={}, body={}", response.statusCode(), endpoint, response.body());
                // 针对 Responses-only 模型误用 Chat Completions 的常见错误做一次自动重试
                if (!endpoint.endsWith("/responses") && containsReasoningParamError(response.body())) {
                    String fallbackEndpoint = buildResponsesEndpoint(baseUrl);
                    log.warn("检测到 reasoning 相关参数错误，自动切换到 Responses API 重试: {}", fallbackEndpoint);
                    return sendRequestViaResponses(content, apiKey, model, fallbackEndpoint);
                }
                throw new RuntimeException("AI请求失败，状态码: " + response.statusCode() + ", 详情: " + response.body());
            }
        } catch (Exception e) {
            log.error("调用AI服务异常", e);
            throw e instanceof RuntimeException ? (RuntimeException) e : new RuntimeException(e);
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private static String firstNonBlank(String... values) {
        for (String value : values) if (!isBlank(value)) return value;
        return "";
    }

    String sendAnthropicRequest(String content, String baseUrl, String token, String model, String maxTokens) {
        JSONObject payload = new JSONObject();
        payload.put("model", model);
        payload.put("max_tokens", Integer.parseInt(maxTokens));
        payload.put("messages", new JSONArray().put(new JSONObject().put("role", "user").put("content", content)));
        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .uri(URI.create(normalizeBaseUrl(baseUrl) + "/v1/messages"))
                .timeout(Duration.ofSeconds(180))
                .header("Content-Type", "application/json")
                .header("anthropic-version", "2023-06-01");
        if (!isBlank(token) && !"PROXY_MANAGED".equals(token)) {
            builder.header("Authorization", "Bearer " + token);
        }
        HttpRequest request = builder.POST(HttpRequest.BodyPublishers.ofString(payload.toString())).build();
        try {
            HttpResponse<String> response = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(60)).build()
                    .send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                throw new IllegalStateException("Anthropic AI请求失败，状态码: " + response.statusCode());
            }
            JSONArray blocks = new JSONObject(response.body()).optJSONArray("content");
            StringBuilder result = new StringBuilder();
            if (blocks != null) {
                for (int i = 0; i < blocks.length(); i++) {
                    JSONObject block = blocks.optJSONObject(i);
                    if (block != null && "text".equals(block.optString("type"))) {
                        result.append(block.optString("text", ""));
                    }
                }
            }
            return result.toString();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Anthropic AI请求中断", e);
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Anthropic AI连接失败", e);
        }
    }

    private String normalizeBaseUrl(String baseUrl) {
        if (baseUrl == null) return "";
        String trimmed = baseUrl.trim();
        return trimmed.endsWith("/") ? trimmed.substring(0, trimmed.length() - 1) : trimmed;
    }

    /**
     * 根据配置构造 chat/completions 端点，避免重复拼接 /v1
     */
    private String buildChatCompletionsEndpoint(String baseUrl) {
        String normalized = normalizeBaseUrl(baseUrl);
        // 如果 baseUrl 已经包含 /v1（常见配置为 https://api.openai.com/v1），则只拼接 /chat/completions
        if (normalized.endsWith("/v1") || normalized.contains("/v1/")) {
            return normalized + "/chat/completions";
        }
        return normalized + "/v1/chat/completions";
    }

    /**
     * 构造 Responses API 端点
     */
    private String buildResponsesEndpoint(String baseUrl) {
        String normalized = normalizeBaseUrl(baseUrl);
        if (normalized.endsWith("/v1") || normalized.contains("/v1/")) {
            return normalized + "/responses";
        }
        return normalized + "/v1/responses";
    }

    /**
     * 粗略识别需要使用 Responses API 的模型（o-系列、4.1、reasoner 等）
     */
    private boolean isResponsesModel(String model) {
        if (model == null) return false;
        String m = model.toLowerCase();
        return m.contains("o1") || m.contains("o3") || m.contains("o4")
                || m.contains("4.1") || m.contains("reasoner")
                || m.contains("4o-mini") || m.contains("gpt-4o-mini");
    }

    /**
     * 检查错误响应中是否包含 reasoning 相关参数错误（如 reasoning.summary unsupported_value）
     */
    private boolean containsReasoningParamError(String body) {
        if (body == null) return false;
        String s = body.toLowerCase();
        return (s.contains("reasoning") && s.contains("unsupported_value"))
                || s.contains("reasoning.summary");
    }

    /**
     * 使用 Responses API 发送一次请求（用于自动降级/重试）
     */
    private String sendRequestViaResponses(String content, String apiKey, String model, String endpoint) {
        int timeoutInSeconds = 60;
        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(timeoutInSeconds))
                .build();

        JSONObject requestData = new JSONObject();
        requestData.put("model", model);
        requestData.put("temperature", 0.5);
        requestData.put("input", content);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(endpoint))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .header("Authorization", "Bearer " + apiKey)
                .header("api-key", apiKey)
                .POST(HttpRequest.BodyPublishers.ofString(requestData.toString()))
                .build();

        try {
            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 200) {
                JSONObject resp = new JSONObject(response.body());
                return extractResponsesText(resp);
            }
            log.error("Responses API 调用失败: status={}, endpoint={}, body={}", response.statusCode(), endpoint, response.body());
            throw new RuntimeException("AI请求失败，状态码: " + response.statusCode() + ", 详情: " + response.body());
        } catch (Exception e) {
            log.error("Responses API 调用异常", e);
            throw e instanceof RuntimeException ? (RuntimeException) e : new RuntimeException(e);
        }
    }

    /** Responses API 返回的文本位于 output 中的 message/content/output_text 项。 */
    static String extractResponsesText(JSONObject response) {
        String outputText = response.optString("output_text", null);
        if (outputText != null && !outputText.isBlank()) return outputText;
        JSONArray output = response.optJSONArray("output");
        StringBuilder text = new StringBuilder();
        if (output != null) {
            for (int i = 0; i < output.length(); i++) {
                JSONObject item = output.optJSONObject(i);
                if (item == null || !"message".equals(item.optString("type"))) continue;
                JSONArray content = item.optJSONArray("content");
                if (content == null) continue;
                for (int j = 0; j < content.length(); j++) {
                    JSONObject part = content.optJSONObject(j);
                    if (part != null && "output_text".equals(part.optString("type"))) {
                        text.append(part.optString("text", ""));
                    }
                }
            }
        }
        if (!text.toString().isBlank()) return text.toString();
        JSONArray choices = response.optJSONArray("choices");
        JSONObject choice = choices == null ? null : choices.optJSONObject(0);
        JSONObject message = choice == null ? null : choice.optJSONObject("message");
        return message == null ? null : message.optString("content", null);
    }

    // ================= 合并的 AI 配置管理方法 =================

    /**
     * 获取AI配置（获取最新一条，如果不存在则创建默认配置）
     */
    @Transactional(readOnly = true)
    public AiEntity getAiConfig() {
        var list = aiMapper.selectList(null);
        AiEntity aiEntity = (list == null || list.isEmpty()) ? null : list.get(list.size() - 1);
        if (aiEntity == null) {
            aiEntity = createDefaultConfig();
        }
        return aiEntity;
    }

    /**
     * 获取所有AI配置
     */
    @Transactional(readOnly = true)
    public java.util.List<AiEntity> getAllAiConfigs() {
        return aiMapper.selectList(null);
    }

    /**
     * 根据ID获取AI配置
     */
    @Transactional(readOnly = true)
    public AiEntity getAiConfigById(Long id) {
        return aiMapper.selectById(id);
    }

    /**
     * 保存或更新AI配置（introduce/prompt）
     */
    @Transactional
    public AiEntity saveOrUpdateAiConfig(String introduce, String prompt) {
        var list = aiMapper.selectList(null);
        AiEntity aiEntity = (list == null || list.isEmpty()) ? null : list.get(list.size() - 1);

        if (aiEntity == null) {
            aiEntity = new AiEntity();
            aiEntity.setIntroduce(introduce);
            aiEntity.setPrompt(prompt);
            aiEntity.setCreatedAt(java.time.LocalDateTime.now());
            aiEntity.setUpdatedAt(java.time.LocalDateTime.now());
            aiMapper.insert(aiEntity);
            log.info("创建新的AI配置，ID: {}", aiEntity.getId());
        } else {
            aiEntity.setIntroduce(introduce);
            aiEntity.setPrompt(prompt);
            aiEntity.setUpdatedAt(java.time.LocalDateTime.now());
            aiMapper.updateById(aiEntity);
            log.info("更新AI配置，ID: {}", aiEntity.getId());
        }

        return aiEntity;
    }

    /**
     * 删除AI配置
     */
    @Transactional
    public boolean deleteAiConfig(Long id) {
        int result = aiMapper.deleteById(id);
        if (result > 0) {
            log.info("删除AI配置成功，ID: {}", id);
            return true;
        }
        return false;
    }

    /**
     * 创建默认配置
     */
    @Transactional
    protected AiEntity createDefaultConfig() {
        AiEntity aiEntity = new AiEntity();
        aiEntity.setIntroduce("请在此填写您的技能介绍");
        aiEntity.setPrompt("请在此填写AI提示词模板");
        aiEntity.setCreatedAt(java.time.LocalDateTime.now());
        aiEntity.setUpdatedAt(java.time.LocalDateTime.now());
        aiMapper.insert(aiEntity);
        log.info("创建默认AI配置，ID: {}", aiEntity.getId());
        return aiEntity;
    }
}