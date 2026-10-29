package com.getjobs.worker.service;

import com.getjobs.worker.manager.PlaywrightManager;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.microsoft.playwright.BrowserContext;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.options.ScreenshotType;
import com.microsoft.playwright.options.SelectOption;
import com.microsoft.playwright.options.WaitUntilState;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Function;

/**
 * 通用浏览器操作：给 agent 在任意网申页面读表单、填字段、上传简历。
 * 所有 Playwright 调用都串行在 PlaywrightManager 的专用线程上。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CustomBrowserService {

    private final PlaywrightManager playwrightManager;

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private volatile Page page;

    private <T> T onPage(Function<Page, T> action) {
        playwrightManager.init();
        return playwrightManager.callOnPlaywright(() -> {
            if (page == null || page.isClosed()) {
                BrowserContext context = playwrightManager.getContext();
                if (context == null) {
                    throw new IllegalStateException("浏览器上下文不可用");
                }
                page = context.newPage();
                page.setDefaultTimeout(15000);
            }
            return action.apply(page);
        });
    }

    public Map<String, Object> open(String url) {
        return onPage(p -> {
            p.navigate(url, new Page.NavigateOptions()
                    .setWaitUntil(WaitUntilState.DOMCONTENTLOADED)
                    .setTimeout(45000));
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("ok", true);
            result.put("url", p.url());
            result.put("title", p.title());
            return result;
        });
    }

    public Map<String, Object> state() {
        return onPage(p -> {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("ok", true);
            result.put("url", p.url());
            result.put("title", p.title());
            return result;
        });
    }

    public String snapshot() {
        return onPage(p -> String.valueOf(p.evaluate(SNAPSHOT_JS)));
    }

    public void click(String ref) {
        onPage(p -> {
            java.util.Set<Page> before = new java.util.LinkedHashSet<>(p.context().pages());
            p.locator(refSelector(ref)).first().click();
            adoptNewPage(p, before);
            return null;
        });
    }

    public void clickText(String text) {
        onPage(p -> {
            java.util.Set<Page> before = new java.util.LinkedHashSet<>(p.context().pages());
            p.getByText(text, new Page.GetByTextOptions().setExact(true))
                    .first()
                    .click();
            adoptNewPage(p, before);
            return null;
        });
    }

    private void adoptNewPage(Page current, java.util.Set<Page> before) {
        try {
            for (Page candidate : current.context().pages()) {
                if (!before.contains(candidate) && !candidate.isClosed()) {
                    this.page = candidate;
                    this.page.setDefaultTimeout(15000);
                    break;
                }
            }
        } catch (Exception ignored) {
            // 没有新页面就保持当前页
        }
    }

    public String links() {
        return onPage(p -> String.valueOf(p.evaluate(LINKS_JS)));
    }

    public String pages() {
        return onPage(p -> {
            var rows = new java.util.ArrayList<java.util.Map<String, Object>>();
            var list = p.context().pages();
            for (int i = 0; i < list.size(); i++) {
                Page item = list.get(i);
                java.util.Map<String, Object> row = new LinkedHashMap<>();
                row.put("index", i);
                row.put("url", item.url());
                row.put("title", item.title());
                row.put("current", item == page);
                rows.add(row);
            }
            try {
                return MAPPER.writeValueAsString(rows);
            } catch (Exception e) {
                return "[]";
            }
        });
    }

    public Map<String, Object> switchPage(int index) {
        return onPage(p -> {
            var list = p.context().pages();
            if (index < 0 || index >= list.size()) {
                throw new IllegalArgumentException("页码不存在: " + index);
            }
            this.page = list.get(index);
            this.page.setDefaultTimeout(15000);
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("ok", true);
            result.put("url", this.page.url());
            result.put("title", this.page.title());
            return result;
        });
    }

    public void fill(String ref, String value) {
        onPage(p -> {
            p.locator(refSelector(ref)).first().fill(value == null ? "" : value);
            return null;
        });
    }

    public void select(String ref, String value) {
        onPage(p -> {
            var locator = p.locator(refSelector(ref)).first();
            try {
                locator.selectOption(new SelectOption().setLabel(value));
            } catch (Exception e) {
                locator.selectOption(value);
            }
            return null;
        });
    }

    public void check(String ref) {
        onPage(p -> {
            p.locator(refSelector(ref)).first().check();
            return null;
        });
    }

    public void press(String key) {
        onPage(p -> {
            p.keyboard().press(key);
            return null;
        });
    }

    public void upload(String ref, String filePath) {
        onPage(p -> {
            p.locator(refSelector(ref)).first().setInputFiles(Paths.get(filePath));
            return null;
        });
    }

    public void uploadFile(String ref, String selector, String filePath) {
        onPage(p -> {
            var locator = selector != null && !selector.isBlank()
                    ? p.locator(selector).first()
                    : p.locator(refSelector(ref)).first();
            locator.setInputFiles(Paths.get(filePath));
            return null;
        });
    }

    public byte[] screenshot() {
        return onPage(p -> p.screenshot(new Page.ScreenshotOptions().setType(ScreenshotType.PNG)));
    }

    private static String refSelector(String ref) {
        if (ref == null || !ref.matches("e\\d{1,3}")) {
            throw new IllegalArgumentException("无效的元素引用: " + ref);
        }
        return "[data-gj-ref=\"" + ref + "\"]";
    }

    private static final String LINKS_JS = """
            () => {
              const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 90);
              return JSON.stringify(
                Array.from(document.querySelectorAll('a[href]'))
                  .map(a => ({ text: clean(a.innerText), href: String(a.getAttribute('href') || '').slice(0, 200) }))
                  .filter(item => item.text || item.href)
                  .slice(0, 200)
              );
            }
            """;

    private static final String SNAPSHOT_JS = """
            () => {
              const MAX = 120;
              const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 90);
              document.querySelectorAll('[data-gj-ref]').forEach(el => el.removeAttribute('data-gj-ref'));
              const labelFor = (el) => {
                const tag = el.tagName;
                if ((tag === 'BUTTON' || tag === 'A' || el.getAttribute('role') === 'button')
                    && (el.innerText || '').trim()) {
                  return clean(el.innerText);
                }
                if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
                const aria = el.getAttribute('aria-label');
                if (aria) return clean(aria);
                const ph = el.getAttribute('placeholder');
                if (ph) return clean(ph);
                if (el.id) {
                  const l = document.querySelector('label[for="' + el.id + '"]');
                  if (l) return clean(l.innerText);
                }
                const wrap = el.closest('label');
                if (wrap) return clean(wrap.innerText);
                const prev = el.previousElementSibling;
                if (prev) return clean(prev.innerText);
                return '';
              };
              const nodes = Array.from(document.querySelectorAll(
                'input,textarea,select,button,a[href],[role="button"],[contenteditable="true"]'
              ));
              const elements = [];
              let idx = 0;
              for (const el of nodes) {
                if (idx >= MAX) break;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                if (rect.width < 2 || rect.height < 2) continue;
                if (style.visibility === 'hidden' || style.display === 'none' || el.disabled) continue;
                if ((el.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
                const ref = 'e' + (idx + 1);
                el.setAttribute('data-gj-ref', ref);
                const item = {
                  ref,
                  tag: el.tagName.toLowerCase(),
                  type: (el.getAttribute('type') || '').toLowerCase(),
                  label: labelFor(el),
                  value: el.value !== undefined ? String(el.value).slice(0, 90) : '',
                  name: clean(el.getAttribute('name')),
                  placeholder: clean(el.getAttribute('placeholder')),
                  text: clean(el.innerText || ''),
                  href: el.tagName === 'A' ? String(el.getAttribute('href') || '').slice(0, 160) : '',
                  checked: !!el.checked
                };
                if (el.tagName === 'SELECT') {
                  item.options = Array.from(el.options).slice(0, 25).map(o => ({
                    text: clean(o.text),
                    value: String(o.value)
                  }));
                }
                elements.push(item);
                idx++;
              }
              return JSON.stringify({
                url: location.href,
                title: document.title,
                text: (document.body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1500),
                elements
              });
            }
            """;
}